# ProxmoxBMC
Based on VirtualBMC @ https://github.com/openstack/virtualbmc

Adapted to make it use the proxmoxer (https://github.com/proxmoxer/proxmoxer) package against the Proxmox VE API instead of libvirt.

## Usage
On a debian based distribution:
Make sure you have python3-pip and python3-venv installed
```
apt-get update && apt-get install python3-pip python3-venv
git clone https://github.com/agnon/proxmoxbmc.git
cd proxmoxbmc
python3 -m venv .env
. .env/bin/activate
pip install -r requirements.txt
python -m setup install
pbmcd # starts the server
# Add a VM
# username = the username used for logging in to the emulated BMC
# password = the password used for logging in to the emulated BMC
# port = the port for the emulated BMC. Specify this if you emulate multiple BMCs on this server
# address = the address to bind to. Binds to 0.0.0.0 by default
# proxmox-address = The address to a proxmox node, prefferably a VIP of the cluster
# token-user = the user that the token belongs to like root@pam
# token-name = the name of the token, for instance ipmi or bmc
# token-value = the actual value of the token
# Example of adding the VMID 123 on port 6625 with admin/password as login using a token for the root user named ipmi
pbmc add --username admin --password password --port 6625 --proxmox-address proxmox.example.org --token-user root@pam --token-name ipmi --token-value {token} 123
# If all went well you should now see it in the list of BMCs
pbmc list
# Now start it
pbmc start 123
```
