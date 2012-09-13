#!/usr/bin/env python26
"""Create pools from pool config files"""

import glob
import os
import sys
import f5utility
import logging
import subprocess
from time import sleep

def main():

    #Create Connection to the f5
    f5 = f5utility.f5Connection()
    print " "
    print "------------------------------"
    print " Syncing Changes"
    print "------------------------------"
    print " "

    # sync the F5s
    f5.config_sync.sync_all()

    print " "
    print "done."

if __name__ == "__main__":
    main()
