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

import configparser
import errno
import multiprocessing
import os
import shutil
import signal

from proxmoxbmc import config as pbmc_config
from proxmoxbmc import exception
from proxmoxbmc import log
from proxmoxbmc import utils
from proxmoxbmc.pbmc import ProxmoxBMC

LOG = log.get_logger()

# BMC status
RUNNING = 'running'
DOWN = 'down'
ERROR = 'error'

DEFAULT_SECTION = 'ProxmoxBMC'

CONF = pbmc_config.get_config()


class ProxmoxBMCManager(object):

    PBMC_OPTIONS = ['username', 'password', 'address', 'port',
                    'vmid', 'proxmox_address', 'token_user', 'token_name',
                    'token_value', 'active']

    def __init__(self):
        super(ProxmoxBMCManager, self).__init__()
        self.config_dir = CONF['default']['config_dir']
        self._running_vmids = {}

    def _parse_config(self, vmid):
        config_path = os.path.join(self.config_dir, vmid, 'config')
        if not os.path.exists(config_path):
            raise exception.VmIdNotFound(vmid=vmid)

        try:
            config = configparser.ConfigParser()
            config.read(config_path)

            bmc = {}
            for item in self.PBMC_OPTIONS:
                try:
                    value = config.get(DEFAULT_SECTION, item)
                except configparser.NoOptionError:
                    value = None

                bmc[item] = value

            # Port needs to be int
            bmc['port'] = config.getint(DEFAULT_SECTION, 'port')

            return bmc

        except OSError:
            raise exception.VmIdNotFound(vmid=vmid)

    def _store_config(self, **options):
        config = configparser.ConfigParser()
        config.add_section(DEFAULT_SECTION)

        for option, value in options.items():
            if value is not None:
                config.set(DEFAULT_SECTION, option, str(value))

        config_path = os.path.join(
            self.config_dir, options['vmid'], 'config'
        )

        with open(config_path, 'w') as f:
            config.write(f)

    def _pbmc_enabled(self, vmid, lets_enable=None, config=None):
        if not config:
            config = self._parse_config(vmid)

        try:
            currently_enabled = utils.str2bool(config['active'])

        except Exception:
            currently_enabled = False

        if (lets_enable is not None
                and lets_enable != currently_enabled):
            config.update(active=lets_enable)
            self._store_config(**config)
            currently_enabled = lets_enable

        return currently_enabled

    def _sync_pbmc_states(self, shutdown=False):
        """Starts/stops pBMC instances

        Walks over pBMC instances configuration, starts
        enabled but dead instances, kills non-configured
        but alive ones.
        """

        def pbmc_runner(bmc_config):
            # The manager process installs a signal handler for SIGTERM to
            # propagate it to children. Return to the default handler.
            signal.signal(signal.SIGTERM, signal.SIG_DFL)

            show_passwords = CONF['default']['show_passwords']

            if show_passwords:
                show_options = bmc_config
            else:
                show_options = utils.mask_dict_password(bmc_config)

            try:
                pbmc = ProxmoxBMC(**bmc_config)

            except Exception as ex:
                LOG.exception(
                    'Error running pBMC with configuration '
                    '%(opts)s: %(error)s', {'opts': show_options,
                                            'error': ex}
                )
                return

            try:
                pbmc.listen(timeout=CONF['ipmi']['session_timeout'])

            except Exception as ex:
                LOG.exception(
                    'Shutdown pBMC for vmid %(vmid)s, cause '
                    '%(error)s', {'vmid': show_options['vmid'],
                                  'error': ex}
                )
                return

        for vmid in os.listdir(self.config_dir):
            if not os.path.isdir(
                    os.path.join(self.config_dir, vmid)
            ):
                continue

            try:
                bmc_config = self._parse_config(vmid)

            except exception.VmIdNotFound:
                continue

            if shutdown:
                lets_enable = False
            else:
                lets_enable = self._pbmc_enabled(
                    vmid, config=bmc_config
                )

            instance = self._running_vmids.get(vmid)

            if lets_enable:

                if not instance or not instance.is_alive():

                    instance = multiprocessing.Process(
                        name='pbmcd-managing-vmid-%s' % vmid,
                        target=pbmc_runner,
                        args=(bmc_config,)
                    )

                    instance.daemon = True
                    instance.start()

                    self._running_vmids[vmid] = instance

                    LOG.info(
                        'Started pBMC instance for vmid '
                        '%(vmid)s', {'vmid': vmid}
                    )

                if not instance.is_alive():
                    LOG.debug(
                        'Found dead pBMC instance for vmid %(vmid)s '
                        '(rc %(rc)s)', {'vmid': vmid,
                                        'rc': instance.exitcode}
                    )

            else:
                if instance:
                    if instance.is_alive():
                        instance.terminate()
                        LOG.info(
                            'Terminated pBMC instance for vmid '
                            '%(vmid)s', {'vmid': vmid}
                        )

                    self._running_vmids.pop(vmid, None)

    def _show(self, vmid):
        bmc_config = self._parse_config(vmid)

        show_passwords = CONF['default']['show_passwords']

        if show_passwords:
            show_options = bmc_config
        else:
            show_options = utils.mask_dict_password(bmc_config)

        instance = self._running_vmids.get(vmid)

        if instance and instance.is_alive():
            show_options['status'] = RUNNING
        elif instance and not instance.is_alive():
            show_options['status'] = ERROR
        else:
            show_options['status'] = DOWN

        return show_options

    def periodic(self, shutdown=False):
        self._sync_pbmc_states(shutdown)

    def add(self, username, password, port, address, vmid, proxmox_address,
            token_user, token_name, token_value, **kwargs):

        # check libvirt's connection and if domain exist prior to adding it
        # utils.check_libvirt_connection_and_domain(
        #     libvirt_uri, domain_name,
        #     sasl_username=libvirt_sasl_username,
        #     sasl_password=libvirt_sasl_password)

        vmid_path = os.path.join(self.config_dir, vmid)

        try:
            os.makedirs(vmid_path)
        except OSError as ex:
            if ex.errno == errno.EEXIST:
                return 1, str(ex)

            msg = ('Failed to create vmid %(vmid)s. '
                   'Error: %(error)s' % {'vmid': vmid, 'error': ex})
            LOG.error(msg)
            return 1, msg

        try:
            self._store_config(vmid=str(vmid),
                               username=username,
                               password=password,
                               port=str(port),
                               address=address,
                               proxmox_address=proxmox_address,
                               token_user=token_user,
                               token_name=token_name,
                               token_value=token_value,      
                               active=False)

        except Exception as ex:
            self.delete(vmid)
            return 1, str(ex)

        return 0, ''

    def delete(self, vmid):
        vmid_path = os.path.join(self.config_dir, vmid)
        if not os.path.exists(vmid_path):
            raise exception.VmIdNotFound(vmid=vmid)

        try:
            self.stop(vmid)
        except exception.ProxmoxBMCError:
            pass

        shutil.rmtree(vmid_path)

        return 0, ''

    def start(self, vmid):
        try:
            bmc_config = self._parse_config(vmid)

        except Exception as ex:
            return 1, str(ex)

        if vmid in self._running_vmids:

            self._sync_pbmc_states()

            if vmid in self._running_vmids:
                LOG.warning(
                    'BMC instance %(vmid)s already running, ignoring '
                    '"start" command' % {'vmid': vmid})
                return 0, ''

        try:
            self._pbmc_enabled(vmid,
                               config=bmc_config,
                               lets_enable=True)

        except Exception as e:
            LOG.exception('Failed to start vmid %s', vmid)
            return 1, ('Failed to start vmid %(vmid)s. Error: '
                       '%(error)s' % {'vmid': vmid, 'error': e})

        self._sync_pbmc_states()

        return 0, ''

    def stop(self, vmid):
        try:
            self._pbmc_enabled(vmid, lets_enable=False)

        except Exception as ex:
            LOG.exception('Failed to stop vmid %s', vmid)
            return 1, str(ex)

        self._sync_pbmc_states()

        return 0, ''

    def list(self):
        rc = 0
        tables = []
        try:
            for vmid in os.listdir(self.config_dir):
                if os.path.isdir(os.path.join(self.config_dir, vmid)):
                    tables.append(self._show(vmid))

        except OSError as e:
            if e.errno == errno.EEXIST:
                rc = 1

        return rc, tables

    def show(self, vmid):
        return 0, list(self._show(vmid).items())
