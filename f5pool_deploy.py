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

    """Create pools from pool config files"""

    #Set directory for pool conf files
    src_dir = 'pools/'

    #Create Connection to the f5
    f5 = f5utility.f5Connection()

    print " "
    print "------------------------------"
    print " Checking Configuration"
    print "------------------------------"
    print " "

    #Create empty queue for pool create/changes
    queue = []

    for infile in sorted(glob.glob(os.path.join(src_dir, '*_pool'))):

        #Build a pool from the config file and check that its valid
        pool = f5.pool.build(infile)

        name = pool['name']
        
        # Check if the pool already exists
        if f5.pool.exists(name):

            # Check if the pool has changed
            if f5.pool.changed(pool):

                # if the pool is valid add it to the queue
                # if the pool is not valid this will fail and exit the script
                if f5.pool.test(pool):

                    print "Marking pool %s for modification" % name
                    pool['operation'] = 'modify'
                    queue.append(pool)

            else:
                print "No Changes made to %s" % name
        else:
            # Check if there is a monitor available for the pool, exit if not.
            monitor_name = f5utility.swap_suffix("_health", name)
            if f5.monitor.exists(monitor_name):

                # Add pool to queue for creation
                print "Marking pool %s for creation" % name
                pool['operation'] = 'create'
                queue.append(pool)

            else:
                print "NO Monitor exists for %s ... STOPPING!" % name
                sys.exit("exit.")


    #Process the queue and commit changes to f5
    print " "
    print "------------------------------"
    print " Committing Changes"
    print "------------------------------"
    print " "

    # If the queue is empty print message, otherwise commit items in queue
    if queue == []:

        print "No Changes to Commit."

    else:

        for pool in queue:
            f5.pool.commit(pool)
            sleep(5)

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
    try:
        with open(os.devnull,'wb') as devnull:
            subprocess.check_call(["/usr/bin/cvs","diff","pools"],stdout=devnull,stderr=devnull)
    except subprocess.CalledProcessError:
        print "Changes have not been committed to cvs.  Run 'cvs diff' confirm the changes and then commit them"
        sys.exit(1)
    main()
