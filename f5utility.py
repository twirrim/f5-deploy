#!/usr/bin/env python26
"""Assorted utilities for managing F5 Load balancers"""

import ConfigParser
import glob
import os
import socket
import sys
import copy
import logging
import suds
from socket import gethostname
import pycontrol.pycontrol as pc
from time import strftime, localtime
import pprint

DEBUG = 0

# changes the suffix on a string separated by _
def swap_suffix(suffix, name):
    """Swaps suffix with specified name"""

    split_array = name.rsplit('_', 1)
    new_name = split_array[0] + suffix

    return new_name

# Handles all pool related stuff
class Pool:
    """pool class manages pools on f5s"""

    config_file = 'f5.cfg'
    existing_pools = 'empty'

    def __init__(self):
        """Initialise the Pool class"""
        # Connect to f5 and save connection
        conn = self.connect()

        # Save some typing
        self.pool = conn.LocalLB.Pool

    def connect(self):
        """Connect to the load balancer"""
        # Set up config file
        config = ConfigParser.ConfigParser()
        config.read(self.config_file)

        # Connect to the F5 using the propery wsdls
        connection = pc.BIGIP(
                            hostname=config.get('LoadBalancer', 'hostname'),
                            username=config.get('LoadBalancer', 'username'),
                            password=config.get('LoadBalancer', 'password'),
                            fromurl=True,
                            wsdls=['LocalLB.Pool'])

        return connection
    
    def exists(self, name):
        """Checks if a pool already exists"""

        if self.existing_pools == 'empty':
            # Get a list of existing pools to check against
            self.existing_pools = self.pool.get_list()

        # Check if Pool Exists
        if name in self.existing_pools:
            exists = True
        else:
            exists = False

        return exists

    # Checks if a pools config matches what the LB has
    def changed(self, pool):
        """Checks if the new pool config matches the existing config"""

        mem_sequence = pool['members']
        name = pool['name']
        method = pool['method']

        # This Section checks if a pool is the same based on it's member's address and port and LB method
        changed = False

        # Check if the lb method is different
        existing_lb_method = self.pool.get_lb_method(pool_names=[name])

        if (method != existing_lb_method[0]):
            changed = True

        existing_mem_sequence = self.pool.get_member(pool_names=[name])
        clean_existing_mem_sequence = existing_mem_sequence[0]

        # Check if the number of pool members is the same, if so then check if the pool members are the same
        if len(clean_existing_mem_sequence) == len(mem_sequence.item):
            # Create a list of existing addresses
            existing_sockets = []
            for existingitem in clean_existing_mem_sequence:
                existing_socket = existingitem.address + ':' + str(existingitem.port)
                existing_sockets.append(existing_socket)

            # Create a list of new addresses
            new_sockets = []
            for new_item in mem_sequence.item:
                new_socket = new_item.address + ':' + new_item.port
                new_sockets.append(new_socket)

            #list of matching addresses
            matching_sockets = list(set(existing_sockets) & set(new_sockets))

            if ((len(matching_sockets) == len(existing_sockets))) is False:
                changed = True

        else:
            changed = True

        return changed

    def build(self, pool_file):
        """Builds a pool from a config file and returns a queue entry"""

        # Set up settings file
        config = ConfigParser.ConfigParser()
        config.read(self.config_file)

        # we'll create an LB Method object. Its attributes look like
        # "lbmeth.LB_METHOD_ROUND_ROBIN", etc.
        lbmeth = self.pool.typefactory.create('LocalLB.LBMethod')

        #little hack so we can reference it
        method = lbmeth.__dict__
        lbmethod = method[config.get('Pool', 'lbmeth')]

        # This is basically a stub holder of member items that we need to wrap up.
        mem_sequence = self.pool.typefactory.create('Common.IPPortDefinitionSequence')

        # Create a 'sequence' of pool members.
        mem_sequence.item = []
        name = os.path.basename(pool_file)
        conf_file = open(pool_file, 'r')

        for line in conf_file:

            # Remove leading and trailing whitespace
            clean_line = line.strip()

            # Break into hostname and port
            url = clean_line.split(':')
            member = self.pool.typefactory.create('Common.IPPortDefinition')

            # Resolve the servername
            member_address = socket.gethostbyname(url[0])
            member.address = member_address
            member.port = url[1]
            mem_sequence.item.append(member)

        # Return a pool dictionary
        pool = {'name': name, 'method': lbmethod, 'members': mem_sequence}
        return pool

    def test(self, pool):
        """Test the pool to make sure its values are valid"""

        name = pool['name']
        method = pool['method']
        members = pool['members']

        tmp_pool = 'tmp_' + name

        # Create a temp pool with the name included in it so we can validate both the name and it's members
        try:
            print "Testing Pool"
            self.pool.create(pool_names=[tmp_pool], lb_methods=[method], members=[members])
            self.attach_monitor(tmp_pool)
            self.pool.delete_pool(pool_names=[tmp_pool])
        except:
            # Cleanup and exit
            self.pool.delete_pool(pool_names=[tmp_pool])
            sys.exit("New Pool Not valid... STOPPING!")

        return True

    def commit(self, pool):
        """Commit the pool changes to the f5"""

        name = pool['name']
        method = pool['method']
        members = pool['members']

        if pool['operation'] == 'create':

            # Create a new pool
            print "Creating Pool: %s" % name
            self.pool.create(pool_names=[name], lb_methods=[method], members=[members])
            self.attach_monitor(name)

        elif pool['operation'] == 'modify':

            # Modify Existing Pool
            print "Modifying Pool: %s" % name

            # Get the existing pool members
            existing_mem_sequence = self.pool.get_member(pool_names=[name])
            clean_existing_mem_sequence = existing_mem_sequence[0]

            # Convert it to format for f5
            # The API says it wants a AddressPort object, but the API lies
            existing_mem_sequence = self.pool.typefactory.create('Common.IPPortDefinitionSequence')
            existing_mem_sequence.item = []

            for member in clean_existing_mem_sequence:
                existing_mem_sequence.item.append(member)

            # Remove the existing members and add new ones
            self.pool.remove_member(pool_names=[name], members=[existing_mem_sequence])
            self.pool.add_member(pool_names=[name], members=[members])
            self.pool.set_lb_method(pool_names=[name], lb_methods=[method])

        return True

    def detach_monitor(self, pool_name):
        """Remove monitors from specified pool"""
        try:
            self.pool.remove_monitor_association([pool_name])
        except suds.WebFault as detail:
            sys.exit(detail)

        return

    # Attaches a monitor to the of the same name as the pool but suffixed with _health instead of _pool
    def attach_monitor(self, pool_name):
        """Attach a monitor of the same name as pool but with _health suffix instead of _pool"""

        # Build a Monitor object with the same name as the pool
        monitor = self.pool.typefactory.create('LocalLB.Pool.MonitorAssociation')
        monitor_rule = self.pool.typefactory.create('LocalLB.MonitorRule')
        monitor_rule.type = "MONITOR_RULE_TYPE_SINGLE"
        monitor_rule.quorum = 0

        # If a tmp attachment then rename it to use the regular monitor
        if pool_name.startswith('tmp_'):
            name_list = pool_name.split('_', 1)
            name = name_list[1]
        else:
            name = pool_name

        monitor_name = swap_suffix("_health", name)
        monitor_rule.monitor_templates = [monitor_name]

        # Assign monitor to pool
        monitor.pool_name = pool_name
        monitor.monitor_rule = monitor_rule
        
        # Set Monitor Association on F5
        print "Attaching Monitor: %s" % monitor_name
        try:
            self.pool.set_monitor_association([monitor])
        except:
            return False

        return True


class Monitor:
    """monitor class manages monitors on f5s"""

    config_file = 'f5.cfg'
    existing_monitors = 'empty'

    def __init__(self):
        """Initialise connection to f5"""
        # Connect to f5 and save connection
        conn = self.connect()

        # Save some typing
        self.monitor = conn.LocalLB.Monitor

    def connect(self):
        """Connect to the F5"""
        # Set up config file
        config = ConfigParser.ConfigParser()
        config.read(self.config_file)

        logging.getLogger('suds.client').setLevel(logging.CRITICAL)
        # Connect to the F5 using the propery wsdls
        connection = pc.BIGIP(
                            hostname=config.get('LoadBalancer', 'hostname'),
                            username=config.get('LoadBalancer', 'username'),
                            password=config.get('LoadBalancer', 'password'),
                            fromurl=True,
                            wsdls=['LocalLB.Monitor'])

        return connection

    def exists(self, name):
        """Check if a monitor already exists"""

        if self.existing_monitors == 'empty':
            # If the list hasnt been obtained, grab a copy from the LB and store it
            self.existing_monitors = self.monitor.get_template_list()

        # Check if monitor Exists
        exists = False
        for monitor in self.existing_monitors:
            if monitor.template_name == name:
                exists = True

        return exists

    def changed(self, monitor):
        """Check if a monitor is different from the current config"""
        ### Ugly hack. Need to figure out a better way to handle this.
        if (monitor['monitor_template']['template_type'] == 'TTYPE_TCP_HALF_OPEN'):
            print "For the moment TCP_HALF_OPEN instances will always flag up as needing changed"
            return 2

        monitor_template = monitor['monitor_template']
        name = monitor_template.template_name
        send_string_value = monitor['send_string_value']
        receive_string_value = monitor['receive_string_value']
        username_string_value = monitor['username_string_value']
        password_string_value = monitor['password_string_value']
        interval = monitor['common_attributes']['interval']
        timeout = monitor['common_attributes']['timeout']

        # Get Existing values from the LB
        existing_send_string = self.monitor.get_template_string_property(template_names = [name], property_types = ['STYPE_SEND'])
        existing_receive_string = self.monitor.get_template_string_property(template_names = [name], property_types = ['STYPE_RECEIVE'])
        existing_template_type = self.monitor.get_template_type(template_names = [name])
        existing_username_string = self.monitor.get_template_string_property(template_names = [name], property_types = ['STYPE_USERNAME'])
        existing_password_string = self.monitor.get_template_string_property(template_names = [name], property_types = ['STYPE_PASSWORD'])
        existing_interval = self.monitor.get_template_integer_property(template_names = [name], property_types = ['ITYPE_INTERVAL'])
        existing_timeout = self.monitor.get_template_integer_property(template_names = [name], property_types = ['ITYPE_TIMEOUT'])

        if DEBUG==1:
            print "Send_string",send_string_value.value,"\n",existing_send_string[0].value
            print "Receive_string",receive_string_value.value,"\n",existing_receive_string[0].value
            print "Template_type",monitor_template.template_type,"\n",existing_template_type[0]
            print "password_string",password_string_value.value,"\n",existing_password_string[0].value
            print "username_string",username_string_value.value,"\n",existing_username_string[0].value
            print "interval",interval,"\n",existing_interval[0].value
            print "timeout",timeout,"\n",existing_timeout[0].value
        
        # Check for changes in send string, receive string or template type

        if ((send_string_value.value == existing_send_string[0].value) and 
           (receive_string_value.value == existing_receive_string[0].value) and
           (monitor_template.template_type == existing_template_type[0]) and
           (interval == existing_interval[0].value) and
           (timeout == existing_timeout[0].value)) is False:

            # Something in the monitor has changed
            if ((monitor_template.template_type != existing_template_type[0]) or (interval != existing_interval[0].value) or (timeout != existing_timeout[0].value)):
                # If any of those attributes is different the whole monitor has to be re-created entirely, they can't be adjusted on the fly
                return 2
            else:
                return 1
        elif ((password_string_value.value == existing_password_string[0].value) and (username_string_value.value == existing_username_string[0].value)) is False:
            if ((password_string_value.value == '' and existing_password_string[0].value == None) and (username_string_value.value == '' and existing_username_string[0].value == None)):
                return 0
            else:
                return 1
        else:
            return 0

    def web_build(self,monitor_spec,name,common_attributes):
        ''' Build HTTP(S) Monitoring Checks'''

        try:
            username_string = monitor_spec.get('Health','username')
            password_string = monitor_spec.get('Health','password')
        except:
            username_string = ""
            password_string = ""

        ## Setup Monitor Structure

        # Creating template
        monitor_template = self.monitor.typefactory.create('LocalLB.Monitor.MonitorTemplate')
        monitor_template.template_name = name
        monitor_template.template_type = monitor_spec.get('Health','type')

        # Create Strings to apply to monitor
        send_string_value = self.monitor.typefactory.create('LocalLB.Monitor.StringValue')
        send_string_value.type.value = 'STYPE_SEND'
        send_string_value.value = monitor_spec.get('Health','send_string')

        # Setting username value
        username_string_value = self.monitor.typefactory.create('LocalLB.Monitor.StringValue')
        username_string_value.type.value = 'STYPE_USERNAME'
        username_string_value.value = username_string

        # Setting password value 
        password_string_value = self.monitor.typefactory.create('LocalLB.Monitor.StringValue')
        password_string_value.type.value = 'STYPE_PASSWORD'
        password_string_value.value = password_string

        # Setting receive string
        receive_string_value = self.monitor.typefactory.create('LocalLB.Monitor.StringValue')
        receive_string_value.type.value = 'STYPE_RECEIVE'
        receive_string_value.value = monitor_spec.get('Health','receive_string')

        monitor = {'monitor_template': monitor_template,
                   'common_attributes': common_attributes,
                   'send_string_value': send_string_value,
                   'receive_string_value': receive_string_value,
                   'username_string_value': username_string_value,
                   'password_string_value': password_string_value}
        return monitor

    def tcp_half_build(self,monitor_spec,name,common_attributes):
        ''' Build TCP_HALF_OPEN Monitoring Checks'''
        
        monitor_template = self.monitor.typefactory.create('LocalLB.Monitor.MonitorTemplate')
        monitor_template.template_name = name
        monitor_template.template_type = monitor_spec.get('Health','type')

        monitor = {'monitor_template': monitor_template,
                   'common_attributes': common_attributes}
        return monitor

    def build(self, monitor_file):
        """Build a monitor object"""

        # Set up settings file
        config = ConfigParser.ConfigParser()
        config.read(self.config_file)
        
        monitor_spec = ConfigParser.ConfigParser()
        monitor_spec.read(monitor_file)
        name = os.path.basename(monitor_file)

        common_attributes = self.monitor.typefactory.create('LocalLB.Monitor.CommonAttributes')
        try:
            common_attributes.interval = int(monitor_spec.get('Health','interval'))
            common_attributes.timeout = int(monitor_spec.get('Health','timeout'))
        except:
            common_attributes.interval = int(config.get('Monitor', 'interval'))
            common_attributes.timeout = int(config.get('Monitor', 'timeout'))
        common_attributes.is_read_only = False
        common_attributes.is_directly_usable = False
        common_attributes.dest_ipport.address_type = config.get('Monitor', 'addresstype')
        common_attributes.dest_ipport.ipport.address = config.get('Monitor', 'address')
        common_attributes.dest_ipport.ipport.port = int(config.get('Monitor', 'port'))

        if (monitor_spec.get('Health','type') == 'TTYPE_HTTP' or monitor_spec.get('Health','type') == 'TTYPE_HTTPS'):
            return Monitor.web_build(self,monitor_spec,name,common_attributes)
        elif (monitor_spec.get('Health','type') == 'TTYPE_TCP_HALF_OPEN'):
            return Monitor.tcp_half_build(self,monitor_spec,name,common_attributes)

        sys.exit("If you got here, You're using an unrecognised monitor type")

    def test(self, monitor):
        """Test a monitor"""
        if (monitor['monitor_template']['template_type'] == 'TTYPE_TCP_HALF_OPEN'):
            return True

        monitor_template = monitor['monitor_template']
        name = monitor_template.template_name
        send_string_value = monitor['send_string_value']
        receive_string_value = monitor['receive_string_value']
        common_attributes = monitor['common_attributes']
        username_string_value = monitor['username_string_value']
        password_string_value = monitor['password_string_value']

        # Create Temp monitor
        try:
            # Change name
            tmp_name = 'tmp_' + name

            # Duplicate monitor Template
            tmp_monitor_template = copy.copy(monitor_template)

            tmp_monitor_template.template_name = tmp_name

            # Create a test monitor
            self.monitor.create_template(templates = [tmp_monitor_template], template_attributes = [common_attributes])
            self.monitor.set_template_string_property(template_names =[tmp_name], values = [send_string_value], property_types = ['STYPE_SEND'])
            self.monitor.set_template_string_property(template_names = [tmp_name], values = [receive_string_value], property_types = ['STYPE_RECEIVE'])
            self.monitor.set_template_string_property(template_names = [tmp_name], values = [username_string_value], property_types = ['STYPE_USERNAME'])
            self.monitor.set_template_string_property(template_names = [tmp_name], values = [password_string_value], property_types = ['STYPE_PASSWORD'])

            # Delete tmp monitor
            self.monitor.delete_template(template_names = [tmp_name])

        except suds.WebFault as detail:
            print "Exception!",detail
            # Cleanup and exit
            self.monitor.delete_template(template_names = [tmp_name])
            sys.exit("New Monitor Not valid... STOPPING!")


        return True

    def commit(self, monitor):
        """Commit changes to the f5"""
        monitor_template = monitor['monitor_template']
        common_attributes = monitor['common_attributes']
        name = monitor_template.template_name

        if monitor['operation'] == 'create':
            # Create the Monitor
            print "Creating %s" % name
            self.monitor.create_template(templates = [monitor_template], template_attributes = [common_attributes])

        elif monitor['operation'] == 'recreate':
            # Recreate the monitor in cases where fundamental changes are involved
            print "Recreating %s" % name

            # Connecting a connection to the F5 pool API
            pool_api = f5Connection().pool
            pool_api.detach_monitor(swap_suffix('_pool',name))

            # Delete existing monitor
            self.monitor.delete_template([name])

            # Create new monitor
            self.monitor.create_template(templates = [monitor_template], template_attributes = [common_attributes])

        elif monitor['operation'] == 'modify':
            print "Modifying Monitor: %s" % name

        if (monitor['monitor_template']['template_type'] == 'TTYPE_HTTP' or monitor['monitor_template']['template_type'] == 'TTYPE_HTTPS'):
            # Add string properties
            send_string_value = monitor['send_string_value']
            receive_string_value = monitor['receive_string_value']
            username_string_value = monitor['username_string_value']
            password_string_value = monitor['password_string_value']
            self.monitor.set_template_string_property(template_names = [name], values = [send_string_value], property_types = ['STYPE_SEND'])
            self.monitor.set_template_string_property(template_names = [name], values = [receive_string_value], property_types = ['STYPE_RECEIVE'])
            self.monitor.set_template_string_property(template_names = [name], values = [username_string_value], property_types = ['STYPE_USERNAME'])
            self.monitor.set_template_string_property(template_names = [name], values = [password_string_value], property_types = ['STYPE_PASSWORD'])

        if monitor['operation'] == 'recreate':
            # Now we've re-created the monitor from scratch we need to re-associate it with its pool
            pool_api.attach_monitor(swap_suffix('_pool',name))
        
        return True

class Irule:
    """monitor class manages monitors on f5s"""

    config_file = 'f5.cfg'

    def __init__(self):
        """initialise connection to f5 and save connection"""
        conn = self.connect()
        #Creating a quick alias to save typing
        self.rule = conn.LocalLB.Rule
        logging.getLogger('suds.client').setLevel(logging.DEBUG)
        logging.getLogger('suds.metrics').setLevel(logging.DEBUG)
        logging.getLogger('suds').setLevel(logging.DEBUG)

    def connect(self):
        """Connect to the F5"""
        # Set up config file
        config = ConfigParser.ConfigParser()
        config.read(self.config_file)

        # Connect to the F5 using the propery wsdls
        connection = pc.BIGIP(
                            hostname=config.get('LoadBalancer', 'hostname'),
                            username=config.get('LoadBalancer', 'username'),
                            password=config.get('LoadBalancer', 'password'),
                            fromurl=True,
                            wsdls=['LocalLB.Rule'])
        return connection

    def rule_build(self, src_dir, dirname):
        """Checks and collates rules under specified directory, returns Final rule"""
    
        # Grab current time
        timestamp = strftime("%a, %d %b %Y %H:%M:%S", localtime())
    
        # Initialise the rule, add the starting lines
        built_rule = []
        built_rule.append("# Last Modified %s from %s\n" % (timestamp, gethostname()))
        built_rule.append("\n")
        built_rule.append('''
when HTTP_REQUEST timing on {

    # These help with various web applications
    HTTP::header insert X-Forwarded-Host [HTTP::host]
    HTTP::header insert X-Forwarded-Server [HTTP::host]


    #Set default of vhost_pool in case no other pool is set by switch
    #Earlier pool statements are overridden by later statements 
    pool vhost_pool

    #Check if hostname matches all known hostnames
        switch -glob [HTTP::host]  {
        ''')

        # Produce a list of all the config files
        filelist = sorted(glob.glob( os.path.join(src_dir, '*.conf')));

        # Clear any legacy temp_rule entries
        try:
            self.rule.delete_rule(['temp_rule'])
        except suds.WebFault:
            # Didn't find an existing rule, no need to worry
            pass

        # Read every file and test using temporary rule
        for infile in filelist:
            temp_rule = []
            temp_rule.append("# Temporary Rule, please delete\n\nwhen HTTP_REQUEST timing on {\n\tswitch -glob [HTTP::host]  {\n")
            print "Validating: " + infile
            conf_file = open(infile,'r')
            for line in conf_file:
                modline = '\t\t%s' % line
                temp_rule.append(modline)
            temp_rule.append("\t}\n}\n")
            # Trying to push the temporary rule to the F5
            temp_def = self.rule.typefactory.create('LocalLB.Rule.RuleDefinition')
            temp_def.rule_name = 'temp_rule'
            temp_def.rule_definition = ''.join(temp_rule)
            try:
                # Attempting to create test rule
                self.rule.create(rules=[temp_def])
                self.rule.delete_rule(['temp_rule'])
            except suds.WebFault as detail:
                # Caught an exception, returning just the error message
                exitmessage = "%s\n%s" % (detail, temp_rule)
                sys.exit(exitmessage)

        # Now we know everything is good, prepare the complete rule
        for infile in filelist:
            conf_file = open(infile,'r')
            for line in conf_file:
                modline = '\t\t%s' % line
                built_rule.append(modline)
            
        # Finish off the rule
        built_rule.append('''
            default {
                #If not matching any known domain names, redirect off to your preferred default location
                HTTP::respond 301 Location "http://foo.bar.baz/"
            }
    }

    #if there are no members in the pool push to vhost_pool which will error and prompt the appropriate error pages for the app 
    if { [active_members [LB::server pool] ] < 1 } {
        pool vhost_pool
    }
}
        ''')
        
        # Push through typefactory to make sure
        # that it gets formatted for SOAP properly

        r_def = self.rule.typefactory.create('LocalLB.Rule.RuleDefinition')
        r_def.rule_name = dirname+'_rule'
        raw_code = ''.join(built_rule)
        # Basic syntax checking
        if (self.syntax_check(raw_code) == 1):
            print "Rule "+dirname+" broken, you have unmatching {}s or ()s"
            sys.exit()
        r_def.rule_definition = raw_code
        # Return the rule
        return r_def

    def syntax_check(self, check_rule):
        """Carries out basic syntax checks rather than upload to F5.  Will expand & re-write as we find more obvious gotchas"""
        if (check_rule.count('{') == check_rule.count('}')):
            if (check_rule.count('(') == check_rule.count(')')):
                return 0
        return 1



class ConfigSync:
    """Synchronises the configuration between f5 loadbalancers""" 
    config_file = 'f5.cfg'

    def __init__(self):
        """Initialise connection to f5"""
        # Connect to f5 and save connection
        conn = self.connect()

        # Save some typing
        self.sync = conn.System.ConfigSync

    # connect to the load balancer
    def connect(self):
        """Connect to f5"""
        # Set up config file
        config = ConfigParser.ConfigParser()
        config.read(self.config_file)
        logging.getLogger('suds.client').setLevel(logging.CRITICAL)

        # Connect to the F5 using the propery wsdls
        connection = pc.BIGIP(
                            hostname=config.get('LoadBalancer', 'hostname'),
                            username=config.get('LoadBalancer', 'username'),
                            password=config.get('LoadBalancer', 'password'),
                            fromurl=True,
                            wsdls=['System.ConfigSync'])

        return connection

    # sync config
    def sync_all(self):
        """Synchronise the configuration files"""

        try:
            # Set sync mode to all
            sync_mode = self.sync.typefactory.create('System.ConfigSync.SyncMode')
            
            # Sync config with other F5
            self.sync.synchronize_configuration(sync_mode.CONFIGSYNC_ALL)
            return True
        except:
            return False


class f5Connection:
    """Initial processes"""


    # Create a pool and monitor object
    def __init__(self):
        """Initialise objects"""
        self.pool = Pool()
        self.monitor = Monitor()
        self.irule = Irule()
        self.config_sync = ConfigSync()
