from common import report, ssh_script

report('smoke-diagnosis',ssh_script('ma-0','remote_diagnose.py',timeout=60))
