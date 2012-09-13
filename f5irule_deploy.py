#!/usr/bin/env python26
"""irule deployment"""

import os
import sys
import f5utility
import suds
import subprocess

def main():
    """Evalutes irules in subdirs, tests them individually on the
    load balancer before collating rules and pushing to f5"""

    f5 = f5utility.f5Connection()

    # Fetching a list of subdirectories which we need to process

    subdirs = os.listdir('./irules')
    for dirs in subdirs:
        if dirs == 'CVS':
            continue
        print " "
        print "------------------------------"
        print " Processing subdirectory %s" % dirs
        print "------------------------------"
        print " "
        full_path = './irules/'+dirs
        if os.path.isdir(full_path):

            # Build the rule

            rule_def = f5.irule.rule_build(full_path, dirs)

            # Check the rule for basic errors

            print "Built rules for "+dirs

            # Check if the rule exists, create if it doesn't

            try:
                rule = f5.irule.rule.query_rule(rule_names=[dirs+"_rule"])
            except:
                rule = f5.irule.rule.create(rules=[rule_def])

            # Modify the rule if it exists already

            try:
                rule = f5.irule.rule.modify_rule(rules=[rule_def])
            except suds.WebFault as detail:
                print "Failed to update the rule, probably due to a syntax error"
                sys.exit(detail)
            f = open(dirs+"_rule",'w')
            f.write(str(rule_def))
            f.close()

            print "Rules uploaded to f5"

    print " "
    print "------------------------------"
    print " Syncing Changes"
    print "------------------------------"
    print " "

    # sync the F5s
    f5.config_sync.sync_all()

      
if __name__ == "__main__":
    try:
        with open(os.devnull,'wb') as devnull:
            subprocess.check_call(["/usr/bin/cvs","diff","irules"],stdout=devnull,stderr=devnull)
    except subprocess.CalledProcessError:
        print "Changes have not been committed to cvs.  Run 'cvs diff' confirm the changes and then commit them"
        sys.exit(1)
    main()
