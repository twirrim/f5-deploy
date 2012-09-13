#!/bin/bash

# This script MUST be run from within this directory or it will not work. To fix this, change to use absolute paths
./f5monitor_deploy.py
./f5pool_deploy.py
./f5irule_deploy.py
