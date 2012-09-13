#!/usr/bin/env python26

"""Create monitors from config files"""

import glob
import os
import sys
import f5utility
import subprocess

def main():

    """Create Monitors from config files"""

    #Set directory for monitor conf files
    src_dir = 'monitors/'

    #Create Connection to the f5
    f5 = f5utility.f5Connection()

    print " "
    print "------------------------------"
    print " Checking Configuration"
    print "------------------------------"
    print " "

    #Create empty queue for monitor create/changes
    queue = []

    for infile in sorted(glob.glob(os.path.join(src_dir, '*_health'))):

        #Build a monitor from the config file and check that its valid
        monitor = f5.monitor.build(infile)

        name = monitor['monitor_template'].template_name

        # Check if the monitor already exists
        if f5.monitor.exists(name):

            # Check if the monitor has changed
            changed = f5.monitor.changed(monitor)
            if (changed == 2):
                if f5.monitor.test(monitor):
                    print "Marking monitor %s for re-creation" % name
                    monitor['operation'] = 'recreate'
                    queue.append(monitor)

            elif (changed == 1):

                # if the monitor is valid add it to the queue
                # if the monitor is not valid this will fail and exit the script
                if f5.monitor.test(monitor):

                    print "Marking monitor %s for modification" % name
                    monitor['operation'] = 'modify'
                    queue.append(monitor)

            else:
                print "No Changes made to %s" % name
        else:

            # Add monitor to queue for creation
            print "Marking monitor %s for creation" % name
            monitor['operation'] = 'create'
            queue.append(monitor)


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

        for monitor in queue:
            f5.monitor.commit(monitor)

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
            subprocess.check_call(["/usr/bin/cvs","diff","monitors"],stdout=devnull,stderr=devnull)
    except subprocess.CalledProcessError:
        print "Changes have not been committed to cvs.  Run 'cvs diff' confirm the changes and then commit them"
        sys.exit(1)
    main()

