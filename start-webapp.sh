#!/bin/sh

logfile=$0.log

# start webapp/webhook-receiver (move to port 6001 to get away from Mac airplay issues)
export FLASK_APP=webapp
export FLASK_ENV=development
export FLASK_DEBUG=1
export PYTHONPATH=.
py=`which python`
if [ -z "$py" ]; then py=`which python3`; fi
"$py" -m flask run -p 6008 2>&1 |tee -a $logfile

