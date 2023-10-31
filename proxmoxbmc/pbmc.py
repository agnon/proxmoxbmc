#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import pyghmi.ipmi.bmc as bmc
import re

from proxmoxer import ProxmoxAPI

#from proxmoxbmc import exception
from proxmoxbmc import log
#from proxmoxbmc import utils

LOG = log.get_logger()

# Power states
POWEROFF = 0
POWERON = 1

# From the IPMI - Intelligent Platform Management Interface Specification
# Second Generation v2.0 Document Revision 1.1 October 1, 2013
# https://www.intel.com/content/dam/www/public/us/en/documents/product-briefs/ipmi-second-gen-interface-spec-v2-rev1-1.pdf
#
# Command failed and can be retried
IPMI_COMMAND_NODE_BUSY = 0xC0
# Invalid data field in request
IPMI_INVALID_DATA = 0xcc

# Boot device maps
# ide2 == cdrom.  It's common in PVE
GET_BOOT_DEVICES_MAP = {
    'net': 4,
    'scsi': 8,
    'ide': 8,
    'cdrom': 0x14
}

SET_BOOT_DEVICES_MAP = {
    'network': 'net0',
    'hd': 'scsi0',
    'optical': 'ide2',
}


class ProxmoxBMC(bmc.Bmc):

    def __init__(self, username, password, port, address,
                 vmid, proxmox_address, token_user, token_name, token_value, **kwargs):
        super(ProxmoxBMC, self).__init__({username: password},
                                         port=port, address=address)
        self.vmid = vmid
        
        # TODO check kwargs for verify_ssl and use if set
        self._proxmox = ProxmoxAPI(proxmox_address, user=token_user, token_name=token_name, token_value=token_value, verify_ssl=False)

    def _locate_vmid(self):
        for pve_node in self._proxmox.nodes.get():
            if str(pve_node['status']) == 'online':
                for vm in self._proxmox.nodes(pve_node['node']).qemu.get():
                    if str(vm['vmid']) == self.vmid:                    
                        return pve_node
            
        return None

    def get_boot_device(self):
        LOG.debug('Get boot device called for %(vmid)s', {'vmid': self.vmid})       
        
        # First we find where in the cluster the VMID is located
        pve_node = self._locate_vmid()

        if (pve_node):
            config = self._proxmox.nodes(pve_node['node']).qemu(f'{self.vmid}').config.get()
            boot_device = re.match(r"^order=([a-z]+)", config['boot'])
            if not boot_device.group(1):
                LOG.error('No boot device selected for VM %(vmid)s', {'vmid': self.vmid})  
            
            if (boot_device.group(1) == 'ide'):
                boot_device_with_number = re.match(r"^order=([a-z0-9]+)", config['boot'])
                if boot_device_with_number.group(1) == 'ide2':
                    return GET_BOOT_DEVICES_MAP['cdrom']

            return GET_BOOT_DEVICES_MAP.get(boot_device.group(1), 0)            

    def set_boot_device(self, bootdevice):
        LOG.debug('Set boot device called for %(vmid)s with boot '
                  'device "%(bootdev)s"', {'vmid': self.vmid,
                                           'bootdev': bootdevice})
        device = SET_BOOT_DEVICES_MAP.get(bootdevice)
        if device is None:
            # Invalid data field in request
            return IPMI_INVALID_DATA

        pve_node = self._locate_vmid()        

        if (pve_node):
            self._proxmox.nodes(pve_node['node']).qemu(f'{self.vmid}').config.post(boot=f'order={device}')            

    def get_power_state(self):
        LOG.debug('Get power state called for %(vmid)s',
                  {'vmid': self.vmid})        
        
        pve_node = self._locate_vmid()
        if (pve_node):
            current_status = self._proxmox.nodes(pve_node['node']).qemu(f'{self.vmid}').status.current.get()
            if current_status['status'] == 'running':
                return POWERON

        return POWEROFF
        
    def pulse_diag(self):
        LOG.debug('Power diag called for %(vmid)s (noop)',
                  {'vmid': self.vmid})

    def power_off(self):
        LOG.debug('Power off called for %(vmid)s',
                  {'vmid': self.vmid})

        pve_node = self._locate_vmid()
        if (pve_node):
            self._proxmox.nodes(pve_node['node']).qemu(f'{self.vmid}').status.stop.post()            

    def power_on(self):
        LOG.debug('Power on called for %(vmid)s',
                  {'vmid': self.vmid})

        pve_node = self._locate_vmid()
        if (pve_node):
            self._proxmox.nodes(pve_node['node']).qemu(f'{self.vmid}').status.start.post()
            
    def power_shutdown(self):
        LOG.debug('Soft power off called for %(vmid)s',
                  {'vmid': self.vmid})
        
        pve_node = self._locate_vmid()
        if (pve_node):
            if (self.get_power_state() == POWERON):
                self._proxmox.nodes(pve_node['node']).qemu(f'{self.vmid}').status.shutdown.post()

    def power_reset(self):
        LOG.debug('Power reset called for %(vmid)s',
                  {'vmid': self.vmid})

        pve_node = self._locate_vmid()
        if (pve_node):
            if (self.get_power_state() == POWERON):
                self._proxmox.nodes(pve_node['node']).qemu(f'{self.vmid}').status.reset.post()
        
