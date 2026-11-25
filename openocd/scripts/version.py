#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file version.py
#
# @section Description
# This script contains QPM openocd version information

class version_info:
    """!
    
    This class provides info about QPM openocd version

    """
    def __init__(self):
        """!

        The version_info object base class initializer.

        @return  An instance of the version_info class.

        """
        self.major_rev_id = 2
        self.minor_rev_id = 0
        self.spin_rev_id = 7

##
#   Initializes an instance of version_info Object
ver_info = version_info()

