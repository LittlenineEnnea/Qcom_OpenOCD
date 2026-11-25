import lldb


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand('command script add -f zephyr.zephyr zephyr')
    print('Zephyr OS commands have been installed. Use "zephyr" to see available commands.')

def zephyr(debugger, command, exe_ctx, result, internal_dict):

    args = command.split()
    command_map = {
        'threads': zephyr_threads,
        'mutexes': zephyr_mutexes,
        'semaphores': zephyr_semaphores,
        'timers': zephyr_timers,
        'stacks': zephyr_stacks,
        'msgqs': zephyr_msgqs,
        'mboxes': zephyr_mboxes,
        'pipes': zephyr_pipes,
        'mem_slabs': zephyr_mem_slabs,
    }

    if len(args) == 0:
        result.PutCString('Usage: zephyr <command>')
        result.PutCString(f'Available commands: {list(command_map.keys())}')
        return

    target = debugger.GetSelectedTarget()
    if not target or not target.IsValid():
        result.PutCString('Error: No valid target selected')
        return

    process = target.GetProcess()
    if not process or not process.IsValid():
        result.PutCString('Error: No valid process')
        return

    thread = process.GetSelectedThread()
    if not thread or not thread.IsValid():
        result.PutCString('Error: No valid thread selected')
        return

    frame = thread.GetSelectedFrame()
    if not frame or not frame.IsValid():
        result.PutCString('Error: No valid frame selected')
        return
    
    # Additional validation to ensure frame is usable
    if not frame.GetThread().IsValid() or not frame.GetThread().GetProcess().IsValid():
        result.PutCString('Error: Frame context is not fully initialized')
        return

    command_func = command_map.get(args[0])
    if command_func:
        command_func(frame, result)
    else:
        result.PutCString('Unknown command: {}'.format(args[0]))

def zephyr_threads(frame, result):
    """Display information about all threads in the system"""
    result.PutCString('Fetching thread information...')
    try:
        _kernel = frame.EvaluateExpression('_kernel')
        threads_ptr = _kernel.GetChildMemberWithName('threads')

        # Thread state bit flags mapping (ordered from LSB to MSB)
        THREAD_STATE_FLAGS = [
            (0x01, 'dummy'),
            (0x02, 'pending'),
            (0x04, 'prestart'),
            (0x08, 'dead'),
            (0x10, 'suspended'),
            (0x20, 'aborting'),
            (0x40, 'suspending'),
            (0x80, 'queued'),
        ]

        # Collect thread info into a list of tuples
        thread_info = []
        while threads_ptr.IsValid() and threads_ptr.GetValueAsUnsigned() != 0:
            try:
                thread_addr = threads_ptr.GetValueAsUnsigned()
                thread_name = threads_ptr.GetChildMemberWithName('name').GetSummary()
                thread_name = thread_name.strip('"') if thread_name else "<unnamed>"
                
                base = threads_ptr.GetChildMemberWithName('base')
                prio = base.GetChildMemberWithName('prio').GetValueAsSigned()
                state_val = base.GetChildMemberWithName('thread_state').GetValueAsUnsigned()
                
                # Decode state flags (maintains order)
                if state_val == 0:
                    state_str = "ready"
                else:
                    states = [name for flag, name in THREAD_STATE_FLAGS if state_val & flag]
                    state_str = "|".join(states) if states else f"unknown(0x{state_val:02x})"
                
                # Get entry point
                entry = threads_ptr.GetChildMemberWithName('entry')
                entry_addr = 0
                entry_name = "None"
                if entry.IsValid():
                    pEntry = entry.GetChildMemberWithName('pEntry')
                    if pEntry.IsValid():
                        entry_addr = pEntry.GetValueAsUnsigned()
                        if entry_addr != 0:
                            target = frame.GetThread().GetProcess().GetTarget()
                            addr = target.ResolveLoadAddress(entry_addr)
                            symbol = addr.GetSymbol()
                            entry_name = symbol.GetName() if symbol.IsValid() else "unknown"

                thread_info.append((thread_addr, thread_name, prio, state_str, entry_addr, entry_name))
                threads_ptr = threads_ptr.GetChildMemberWithName('next_thread')
            except Exception as e:
                result.PutCString(f"Error processing thread at {hex(threads_ptr.GetValueAsUnsigned())}: {str(e)}")
                break

        if not thread_info:
            result.PutCString("No threads found")
            return

        # Build table
        table = f"{'Address':<18} {'Name':<25} {'Prio':<6} {'State':<20} {'Entry':<50}\n"
        table += "-" * 119 + "\n"
        for thread_addr, name, prio, state, entry_addr, entry_name in thread_info:
            prio_str = f"{prio:>3}."
            entry_str = f"{hex(entry_addr)[2:].upper()} {entry_name}" if entry_addr != 0 else "None"
            table += f"{hex(thread_addr):<18} {name:<25} {prio_str:<6} {state:<20} {entry_str:<50}\n"

        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_threads: {str(e)}")


def zephyr_sys_track_iter(start_obj, max_iterations=1000):
    """Iterator for Zephyr object tracking lists with safety limits"""
    count = 0
    
    if not start_obj.IsValid():
        print(f"Warning: Invalid start object provided to iterator")
        return
    
    while start_obj.IsValid() and start_obj.GetValueAsUnsigned() != 0 and count < max_iterations:
        count += 1
        yield start_obj
        
        next_obj = start_obj.GetChildMemberWithName('_obj_track_next')
        
        if not next_obj.IsValid():
            print(f"Warning: Invalid next pointer at object {hex(start_obj.GetValueAsUnsigned())} after {count} iterations")
            break
        
        # Break if we're stuck in a loop
        if next_obj.GetValueAsUnsigned() == start_obj.GetValueAsUnsigned():
            print(f"Warning: Detected loop in tracking list at object {hex(start_obj.GetValueAsUnsigned())}")
            break
            
        start_obj = next_obj
        
    if count >= max_iterations:
        print(f"Warning: Reached maximum iteration count ({max_iterations}) in tracking list")


def count_waiting_threads(obj, obj_addr, max_threads=1000):
    """Count waiting threads in a wait_q structure. Returns 0 if none waiting.
    
    Args:
        obj: The object containing the wait_q structure
        obj_addr: Address of the object
        max_threads: Maximum number of threads to count (safety limit)
    
    Returns:
        Number of waiting threads, or 0 if none waiting
    """
    try:
        wait_q = obj.GetChildMemberWithName('wait_q')
        if not wait_q.IsValid():
            return 0
            
        waitq = wait_q.GetChildMemberWithName('waitq')
        if not waitq.IsValid():
            return 0
            
        head = waitq.GetChildMemberWithName('head')
        if not head.IsValid() or head.GetValueAsUnsigned() == 0:
            return 0
            
        head_addr = head.GetValueAsUnsigned()
        waitq_addr = waitq.GetValueAsUnsigned()
        
        # Check if head is pointing to the waitq structure itself (empty list)
        if head_addr == waitq_addr or head_addr == obj_addr:
            return 0
            
        # Count threads in the wait queue with safety limit
        count = 1
        visited = set([head_addr])  # Track visited addresses to detect loops
        
        try:
            current = head
            next_ptr = current.GetChildMemberWithName('next')
            
            while (next_ptr.IsValid() and 
                   next_ptr.GetValueAsUnsigned() != 0 and 
                   next_ptr.GetValueAsUnsigned() != head_addr and 
                   count < max_threads):
                
                next_addr = next_ptr.GetValueAsUnsigned()
                
                # Detect circular references that don't loop back to head
                if next_addr in visited:
                    print(f"Warning: Detected circular reference in wait queue at {hex(next_addr)}")
                    break
                    
                visited.add(next_addr)
                count += 1
                current = next_ptr
                next_ptr = current.GetChildMemberWithName('next')
                
            if count >= max_threads:
                print(f"Warning: Reached maximum thread count ({max_threads}) in wait queue")
                
        except Exception as e:
            # Return the count we have so far if iteration fails
            print(f"Warning: Error while counting waiting threads: {str(e)}")
            pass
            
        return count
    except Exception as e:
        print(f"Warning: Error counting waiting threads: {str(e)}")
        return 0


def get_waiting_thread_name(obj, obj_addr, frame, max_threads=100):
    """Get the names of all waiting threads as a comma-separated list, or 'none' if no threads waiting"""
    try:
        wait_q = obj.GetChildMemberWithName('wait_q')
        if not wait_q.IsValid():
            return "none"
            
        waitq = wait_q.GetChildMemberWithName('waitq')
        if not waitq.IsValid():
            return "none"
            
        head = waitq.GetChildMemberWithName('head')
        if not head.IsValid() or head.GetValueAsUnsigned() == 0:
            return "none"
            
        head_addr = head.GetValueAsUnsigned()
        waitq_addr = waitq.GetValueAsUnsigned()
        
        # Check if head is pointing to the waitq structure itself (empty list)
        if head_addr == waitq_addr or head_addr == obj_addr:
            return "none"
            
        # Collect all waiting thread names
        target = frame.GetThread().GetProcess().GetTarget()
        thread_type = target.FindFirstType('struct k_thread')
        if not thread_type.IsValid():
            return "<unknown>"
            
        thread_names = []
        current_addr = head_addr
        visited = set()
        count = 0
        
        while current_addr != 0 and count < max_threads:
            # Prevent infinite loops
            if current_addr in visited:
                break
            visited.add(current_addr)
            count += 1
            
            try:
                # Get the thread at current address
                thread_ptr = frame.EvaluateExpression(f"(struct k_thread *){current_addr}")
                if thread_ptr.IsValid():
                    name_obj = thread_ptr.GetChildMemberWithName('name')
                    if name_obj.IsValid():
                        thread_name = name_obj.GetSummary()
                        if thread_name:
                            thread_names.append(thread_name.strip('"'))
                        else:
                            thread_names.append("<unnamed>")
                    else:
                        thread_names.append("<unnamed>")
                    
                    # Get next thread in wait queue
                    next_ptr = thread_ptr.GetChildMemberWithName('next')
                    if next_ptr.IsValid():
                        next_addr = next_ptr.GetValueAsUnsigned()
                        # Check if we've looped back to head (circular list)
                        if next_addr == head_addr or next_addr == waitq_addr or next_addr == obj_addr:
                            break
                        current_addr = next_addr
                    else:
                        break
                else:
                    break
            except Exception as e:
                print(f"Warning: Error getting thread name: {str(e)}")
                break
        
        if not thread_names:
            return "none"
        
        # Return comma-separated list of thread names
        return ", ".join(thread_names)
    except Exception as e:
        print(f"Warning: Error in get_waiting_thread_name: {str(e)}")
        return "none"


def format_table(headers, rows, column_widths=None):
    """Format data as a table with headers and rows"""
    if column_widths is None:
        column_widths = [len(h) + 2 for h in headers]
    
    # Build header
    header_line = "".join(f"{h:<{w}}" for h, w in zip(headers, column_widths))
    separator = "-" * sum(column_widths)
    
    # Build rows
    table_lines = [header_line, separator]
    for row in rows:
        row_line = "".join(f"{str(cell):<{w}}" for cell, w in zip(row, column_widths))
        table_lines.append(row_line)
    
    return "\n".join(table_lines)


def get_tracked_objects(frame, tracking_list_name, error_msg_prefix):
    """Get tracked objects from a Zephyr tracking list"""
    tracked_ptr = frame.EvaluateExpression(tracking_list_name)
    
    if not tracked_ptr.IsValid():
        return None, f"Error: Could not find {tracking_list_name} symbol\nObject tracking may not be enabled (CONFIG_OBJECT_TRACING=y required)"
        
    if tracked_ptr.GetValueAsUnsigned() == 0:
        return [], f"No {error_msg_prefix} found (tracking list is empty)"
        
    return tracked_ptr, None

def zephyr_mutexes(frame, result):
    """Display information about all mutexes in the system"""
    result.PutCString('Fetching mutex information...')
    try:
        result.PutCString("Tracked Mutex Objects:")
        result.PutCString("-" * 30)
        
        mutexes_ptr, error = get_tracked_objects(frame, '_track_list_k_mutex', 'mutexes')
        if error:
            result.PutCString(error)
            return
            
        # Collect mutex info
        mutex_info = []
        for mutex_obj in zephyr_sys_track_iter(mutexes_ptr):
            try:
                mutex_addr = mutex_obj.GetValueAsUnsigned()
                owner = mutex_obj.GetChildMemberWithName('owner')
                owner_str = owner.GetSummary().strip('"') if owner.IsValid() and owner.GetSummary() else "None"
                lock_count = mutex_obj.GetChildMemberWithName('lock_count').GetValueAsUnsigned()
                waiting_threads = count_waiting_threads(mutex_obj, mutex_addr)
                
                mutex_info.append((
                    hex(mutex_addr),
                    owner_str,
                    lock_count,
                    "none" if waiting_threads == 0 else str(waiting_threads)
                ))
            except Exception as e:
                result.PutCString(f"Error processing mutex {hex(mutex_obj.GetValueAsUnsigned())}: {str(e)}")
        
        if not mutex_info:
            result.PutCString("No mutexes found in tracking list")
            return
            
        # Format and output table
        table = format_table(
            ['Address', 'Owner', 'Lock Count', 'Waiting'],
            mutex_info,
            [18, 30, 12, 10]
        )
        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_mutexes: {str(e)}")

def zephyr_semaphores(frame, result):
    """Display information about all semaphores in the system"""
    result.PutCString('Fetching semaphore information...')
    try:
        result.PutCString("Tracked Semaphore Objects:")
        result.PutCString("-" * 30)
        
        semaphores_ptr, error = get_tracked_objects(frame, '_track_list_k_sem', 'semaphores')
        if error:
            result.PutCString(error)
            return
            
        # Collect semaphore info
        sem_info = []
        for semaphore_obj in zephyr_sys_track_iter(semaphores_ptr):
            try:
                sem_addr = semaphore_obj.GetValueAsUnsigned()
                count = semaphore_obj.GetChildMemberWithName('count').GetValueAsUnsigned()
                limit = semaphore_obj.GetChildMemberWithName('limit').GetValueAsUnsigned()
                waiting_threads = count_waiting_threads(semaphore_obj, sem_addr)
                
                sem_info.append((
                    hex(sem_addr),
                    count,
                    limit,
                    "none" if waiting_threads == 0 else str(waiting_threads)
                ))
            except Exception as e:
                result.PutCString(f"Error processing semaphore {hex(semaphore_obj.GetValueAsUnsigned())}: {str(e)}")
        
        if not sem_info:
            result.PutCString("No semaphores found in tracking list")
            return
            
        # Format and output table
        table = format_table(
            ['Address', 'Count', 'Limit', 'Waiting'],
            sem_info,
            [18, 10, 10, 10]
        )
        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_semaphores: {str(e)}")

def zephyr_timers(frame, result):
    """Display information about all timers in the system"""
    result.PutCString('Fetching timer information...')
    try:
        result.PutCString("Tracked Timer Objects:")
        result.PutCString("-" * 30)
        
        timers_ptr, error = get_tracked_objects(frame, '_track_list_k_timer', 'timers')
        if error:
            result.PutCString(error)
            return
        
        # Collect timer info
        timer_info = []
        target = frame.GetThread().GetProcess().GetTarget()
        
        for timer_obj in zephyr_sys_track_iter(timers_ptr):
            try:
                timer_addr = timer_obj.GetValueAsUnsigned()
                period_obj = timer_obj.GetChildMemberWithName('period')
                period = period_obj.GetValueAsUnsigned() if period_obj.IsValid() else 0
                
                # Get expiry function name
                expiry_fn = timer_obj.GetChildMemberWithName('expiry_fn')
                expiry_addr = expiry_fn.GetValueAsUnsigned() if expiry_fn.IsValid() else 0
                expiry_name = "None"
                if expiry_addr != 0:
                    addr = target.ResolveLoadAddress(expiry_addr)
                    symbol = addr.GetSymbol()
                    expiry_name = symbol.GetName() if symbol.IsValid() else f"0x{expiry_addr:x}"
                
                timer_info.append((hex(timer_addr), expiry_name, period))
            except Exception as e:
                result.PutCString(f"Error processing timer {hex(timer_obj.GetValueAsUnsigned())}: {str(e)}")
        
        if not timer_info:
            result.PutCString("No timers found in tracking list")
            return
        
        # Format and output table
        table = format_table(
            ['Address', 'Expiry Function', 'Period'],
            timer_info,
            [18, 40, 12]
        )
        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_timers: {str(e)}")

def zephyr_stacks(frame, result):
    """Display information about all stacks in the system"""
    result.PutCString('Fetching stack information...')
    try:
        result.PutCString("Tracked Stack Objects:")
        result.PutCString("-" * 30)
        
        stacks_ptr, error = get_tracked_objects(frame, '_track_list_k_stack', 'stacks')
        if error:
            result.PutCString(error)
            return
            
        # Collect stack info
        stack_info = []
        for stack_obj in zephyr_sys_track_iter(stacks_ptr):
            try:
                stack_addr = stack_obj.GetValueAsUnsigned()
                base = stack_obj.GetChildMemberWithName('base').GetValueAsUnsigned()
                next_val = stack_obj.GetChildMemberWithName('next').GetValueAsUnsigned()
                top = stack_obj.GetChildMemberWithName('top').GetValueAsUnsigned()
                size = stack_obj.GetChildMemberWithName('base').GetType().GetByteSize()
                used = (next_val - base) // size if size > 0 else 0
                max_size = (top - base) // size if size > 0 else 0
                
                stack_info.append((
                    hex(stack_addr),
                    hex(base),
                    max_size,
                    used
                ))
            except Exception as e:
                result.PutCString(f"Error processing stack {hex(stack_obj.GetValueAsUnsigned())}: {str(e)}")
                
        if not stack_info:
            result.PutCString("No stacks found in tracking list")
            return
            
        # Format and output table
        table = format_table(
            ['Address', 'Base', 'Max Size', 'Used'],
            stack_info,
            [18, 18, 12, 12]
        )
        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_stacks: {str(e)}")

def zephyr_msgqs(frame, result):
    """Display information about all message queues in the system"""
    result.PutCString('Fetching message queue information...')
    try:
        result.PutCString("Tracked Message Queue Objects:")
        result.PutCString("-" * 30)
        
        msgqs_ptr, error = get_tracked_objects(frame, '_track_list_k_msgq', 'message queues')
        if error:
            result.PutCString(error)
            return
            
        # Collect message queue info
        msgq_info = []
        for msgq_obj in zephyr_sys_track_iter(msgqs_ptr):
            try:
                msgq_addr = msgq_obj.GetValueAsUnsigned()
                buffer_start = msgq_obj.GetChildMemberWithName('buffer_start').GetValueAsUnsigned()
                msg_size = msgq_obj.GetChildMemberWithName('msg_size').GetValueAsUnsigned()
                used_msgs = msgq_obj.GetChildMemberWithName('used_msgs').GetValueAsUnsigned()
                waiting_threads = count_waiting_threads(msgq_obj, msgq_addr)
                
                msgq_info.append((
                    hex(msgq_addr),
                    hex(buffer_start),
                    msg_size,
                    used_msgs,
                    "none" if waiting_threads == 0 else str(waiting_threads)
                ))
            except Exception as e:
                result.PutCString(f"Error processing message queue {hex(msgq_obj.GetValueAsUnsigned())}: {str(e)}")
        
        if not msgq_info:
            result.PutCString("No message queues found in tracking list")
            return
            
        # Format and output table
        table = format_table(
            ['Address', 'Buffer', 'Size', 'Used', 'Waiting'],
            msgq_info,
            [18, 18, 10, 10, 10]
        )
        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_msgqs: {str(e)}")

def zephyr_pipes(frame, result):
    """Display information about all pipes in the system"""
    result.PutCString('Fetching pipe information...')
    try:
        result.PutCString("Tracked Pipe Objects:")
        result.PutCString("-" * 30)
        
        pipes_ptr, error = get_tracked_objects(frame, '_track_list_k_pipe', 'pipes')
        if error:
            result.PutCString(error)
            return
            
        # Collect pipe info
        pipe_info = []
        for pipe_obj in zephyr_sys_track_iter(pipes_ptr):
            try:
                pipe_addr = pipe_obj.GetValueAsUnsigned()
                
                # Get ring buffer information
                ring_buf = pipe_obj.GetChildMemberWithName('buf')
                buffer = ring_buf.GetChildMemberWithName('buffer').GetValueAsUnsigned()
                size = ring_buf.GetChildMemberWithName('size').GetValueAsUnsigned()
                used = ring_buf.GetChildMemberWithName('used').GetValueAsUnsigned()
                
                # Count waiting threads using the common function
                waiting_count = count_waiting_threads(pipe_obj, pipe_addr)
                waiting_str = "none" if waiting_count == 0 else str(waiting_count)
                
                pipe_info.append((
                    hex(pipe_addr),
                    hex(buffer),
                    size,
                    used,
                    waiting_str
                ))
            except Exception as e:
                result.PutCString(f"Error processing pipe {hex(pipe_obj.GetValueAsUnsigned())}: {str(e)}")
        
        if not pipe_info:
            result.PutCString("No pipes found in tracking list")
            return
            
        # Format and output table
        table = format_table(
            ['Address', 'Buffer', 'Size', 'Used', 'Waiting'],
            pipe_info,
            [18, 18, 10, 10, 10]
        )
        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_pipes: {str(e)}")


def zephyr_mboxes(frame, result):
    """Display information about all mailboxes in the system"""
    result.PutCString('Fetching mailbox information...')
    try:
        result.PutCString("Tracked Mailbox Objects:")
        result.PutCString("-" * 30)
        
        mboxes_ptr, error = get_tracked_objects(frame, '_track_list_k_mbox', 'mailboxes')
        if error:
            result.PutCString(error)
            return
            
        # Collect mailbox info
        mbox_info = []
        for mbox_obj in zephyr_sys_track_iter(mboxes_ptr):
            try:
                mbox_addr = mbox_obj.GetValueAsUnsigned()
                
                # Extract mailbox details
                tx_msg_queue = mbox_obj.GetChildMemberWithName('tx_msg_queue')
                rx_msg_queue = mbox_obj.GetChildMemberWithName('rx_msg_queue')
                
                # Get message counts
                tx_count = 0
                rx_count = 0
                if tx_msg_queue.IsValid():
                    tx_count_obj = tx_msg_queue.GetChildMemberWithName('used_msgs')
                    if tx_count_obj.IsValid():
                        tx_count = tx_count_obj.GetValueAsUnsigned()
                        
                if rx_msg_queue.IsValid():
                    rx_count_obj = rx_msg_queue.GetChildMemberWithName('used_msgs')
                    if rx_count_obj.IsValid():
                        rx_count = rx_count_obj.GetValueAsUnsigned()
                
                # Get waiting thread counts using the common function
                tx_waiting = count_waiting_threads(tx_msg_queue, tx_msg_queue.GetValueAsUnsigned()) if tx_msg_queue.IsValid() else 0
                rx_waiting = count_waiting_threads(rx_msg_queue, rx_msg_queue.GetValueAsUnsigned()) if rx_msg_queue.IsValid() else 0
                
                mbox_info.append((
                    hex(mbox_addr),
                    hex(tx_msg_queue.GetValueAsUnsigned()) if tx_msg_queue.IsValid() else "0x0",
                    tx_count,
                    "none" if tx_waiting == 0 else str(tx_waiting),
                    hex(rx_msg_queue.GetValueAsUnsigned()) if rx_msg_queue.IsValid() else "0x0",
                    rx_count,
                    "none" if rx_waiting == 0 else str(rx_waiting)
                ))
            except Exception as e:
                result.PutCString(f"Error processing mailbox {hex(mbox_obj.GetValueAsUnsigned())}: {str(e)}")
        
        if not mbox_info:
            result.PutCString("No mailboxes found in tracking list")
            return
            
        # Format and output table
        table = format_table(
            ['Address', 'TX Queue', 'TX Msgs', 'TX Wait', 'RX Queue', 'RX Msgs', 'RX Wait'],
            mbox_info,
            [18, 18, 10, 10, 18, 10, 10]
        )
        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_mboxes: {str(e)}")


def zephyr_mem_slabs(frame, result):
    """Display information about tracked memory slab objects"""
    result.PutCString('Fetching memory slab information...')
    try:
        result.PutCString("Tracked Memory Slab Objects:")
        result.PutCString("-" * 30)
        
        mem_slabs_ptr, error = get_tracked_objects(frame, '_track_list_k_mem_slab', 'memory slabs')
        if error:
            result.PutCString(error)
            return
            
        # Collect memory slab info
        mem_slab_info = []
        for mem_slab_obj in zephyr_sys_track_iter(mem_slabs_ptr):
            try:
                mem_slab_addr = mem_slab_obj.GetValueAsUnsigned()
                buffer_obj = mem_slab_obj.GetChildMemberWithName('buffer')
                buffer_addr = buffer_obj.GetValueAsUnsigned() if buffer_obj.IsValid() else 0
                
                # Get info structure
                info_obj = mem_slab_obj.GetChildMemberWithName('info')
                if info_obj.IsValid():
                    num_blocks = info_obj.GetChildMemberWithName('num_blocks').GetValueAsUnsigned()
                    block_size = info_obj.GetChildMemberWithName('block_size').GetValueAsUnsigned()
                    num_used = info_obj.GetChildMemberWithName('num_used').GetValueAsUnsigned()
                else:
                    num_blocks = block_size = num_used = 0
                
                waiting_thread_name = get_waiting_thread_name(mem_slab_obj, mem_slab_addr, frame)
                
                mem_slab_info.append((
                    hex(mem_slab_addr),
                    hex(buffer_addr),
                    block_size,
                    num_blocks,
                    num_used,
                    waiting_thread_name
                ))
            except Exception as e:
                result.PutCString(f"Error processing memory slab {hex(mem_slab_obj.GetValueAsUnsigned())}: {str(e)}")
        
        if not mem_slab_info:
            result.PutCString("No memory slabs found in tracking list")
            return
            
        # Format and output table
        table = format_table(
            ['Address', 'Buffer', 'BlkSize', '#Blks', '#Used', 'Waiting'],
            mem_slab_info,
            [18, 18, 10, 10, 10, 30]
        )
        result.PutCString(table)
    except Exception as e:
        result.PutCString(f"Error in zephyr_mem_slabs: {str(e)}")