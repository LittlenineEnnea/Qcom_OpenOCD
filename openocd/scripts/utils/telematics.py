#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file telematics.py
#
# @section Description
# This script is used for performing telematics send data to the server
import socket
import time
import platform
from utils.debug_context import logger
from utils.debug_context import g_dbgContext

def get_host_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        logger.error("Unable to retrieve HOST IP address")

def send_telematics_data(data):
    logger.info("telematics function invoked")
    # this exception is added to skip telematics data collection while using hexagon-lldb
    try:
        from telematicsv2_py_client.enums.environment import Environment
        from telematicsv2_py_client.enums.applicationType import ApplicationType
        from telematicsv2_py_client.enums.hostArchitecture import HostArchitecture
        from telematicsv2_py_client.enums.hostOs import HostOs
        from telematicsv2_py_client.src.telematics_client import ITelematicsContext, TelematicsClientManager
    except:
        logger.error("telematics data collection failed")
        return
    osName = platform.system()
    g_dbgContext.cur_time = time.time()

    if g_dbgContext.pre_time == None:
        g_dbgContext.pre_time = g_dbgContext.cur_time
    else:
        time_elapsed = float(g_dbgContext.cur_time - g_dbgContext.pre_time)
        if time_elapsed > 120.00:
            pass
        else:
            return
        
    ip = get_host_ip()
    list_data = data
    version = str(list_data["version"])
    del list_data["version"]
    
    if (osName == 'Windows'):
        operating_system = HostOs.WINDOWS
        host_os_version = str(platform.version())
    elif (osName == 'Linux'):
        operating_system = HostOs.LINUX
        host_os_version = str(platform.version())
    
    telematicsContext = ITelematicsContext(
    productVersion = version,
    deviceId = ip,
    hostOs = operating_system,
    hostOsVersion = host_os_version,
    hostArchitecture = HostArchitecture.X86,
    eventDataVersion = "1.0",
    applicationType=ApplicationType.DESKTOP)

    eventPayload = list_data
    key="RAGbGFGUr0sxce1NAQzoD5n8cUi6mgyaqQYCTG8qH0zr2oX6"
    telematicsHandler = TelematicsClientManager()
    tmp = telematicsHandler.initialize(productId="ebe459f6-acca-11e9-84a0-06e1158024a8", environment=Environment.PROD, telematicsContext=telematicsContext,authToken=key)
    tmp1 = telematicsHandler.trackEvent("connect", eventPayload)
    tmp2 = telematicsHandler.destroy()
    logger.info("data sent successfully")