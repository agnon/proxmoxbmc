# Copyright 2017 Red Hat, Inc.
# All Rights Reserved.
#
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

import json
import signal
import sys

import zmq

from proxmoxbmc import config as pbmc_config
from proxmoxbmc import exception
from proxmoxbmc import log
from proxmoxbmc.manager import ProxmoxBMCManager

CONF = pbmc_config.get_config()

LOG = log.get_logger()

TIMER_PERIOD = 3000  # milliseconds


def main_loop(pbmc_manager, handle_command):
    """Server part of the CLI control interface

    Receives JSON messages from ZMQ socket, calls the command handler and
    sends JSON response back to the client.

    Client builds requests out of its command-line options which
    include the command (e.g. `start`, `list` etc) and command-specific
    options.

    Server handles the commands and responds with a JSON document which
    contains at least the `rc` and `msg` attributes, used to indicate the
    outcome of the command, and optionally 2-D table conveyed through the
    `header` and `rows` attributes pointing to lists of cell values.
    """
    server_port = CONF['default']['server_port']

    context = socket = None

    try:
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.setsockopt(zmq.LINGER, 5)
        socket.bind("tcp://127.0.0.1:%s" % server_port)

        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)

        LOG.info('Started pBMC server on port %s', server_port)

        while True:
            socks = dict(poller.poll(timeout=TIMER_PERIOD))
            if socket in socks and socks[socket] == zmq.POLLIN:
                message = socket.recv()
            else:
                pbmc_manager.periodic()
                continue

            try:
                data_in = json.loads(message.decode('utf-8'))

            except ValueError as ex:
                LOG.warning(
                    'Control server request deserialization error: '
                    '%(error)s', {'error': ex}
                )
                continue

            LOG.debug('Command request data: %(request)s',
                      {'request': data_in})

            try:
                data_out = handle_command(pbmc_manager, data_in)

            except exception.ProxmoxBMCError as ex:
                msg = 'Command failed: %(error)s' % {'error': ex}
                LOG.error(msg)
                data_out = {
                    'rc': 1,
                    'msg': [msg]
                }

            LOG.debug('Command response data: %(response)s',
                      {'response': data_out})

            try:
                message = json.dumps(data_out)

            except ValueError as ex:
                LOG.warning(
                    'Control server response serialization error: '
                    '%(error)s', {'error': ex}
                )
                continue

            socket.send(message.encode('utf-8'))

    finally:
        if socket:
            socket.close()
        if context:
            context.destroy()


def command_dispatcher(pbmc_manager, data_in):
    """Control CLI command dispatcher

    Calls pBMC manager to execute commands, implements uniform
    dictionary-based interface to the caller.
    """
    command = data_in.pop('command')

    LOG.debug('Running "%(cmd)s" command handler', {'cmd': command})

    if command == 'add':

        # Check input
        token_value = data_in['token_value']
        token_name = data_in['token_name']
        token_user = data_in['token_user']
        vmid = data_in['vmid']
        
        if not all((token_value, token_name, token_user, vmid)):
            error = ("You need to pass in token user/name/value for this to work")
            return {'msg': [error], 'rc': 1}

        rc, msg = pbmc_manager.add(**data_in)

        return {
            'rc': rc,
            'msg': [msg] if msg else []
        }

    elif command == 'delete':
        data_out = [pbmc_manager.delete(vmid)
                    for vmid in set(data_in['vmids'])]
        return {
            'rc': max(rc for rc, msg in data_out),
            'msg': [msg for rc, msg in data_out if msg],
        }

    elif command == 'start':
        data_out = [pbmc_manager.start(vmid)
                    for vmid in set(data_in['vmids'])]
        return {
            'rc': max(rc for rc, msg in data_out),
            'msg': [msg for rc, msg in data_out if msg],
        }

    elif command == 'stop':
        data_out = [pbmc_manager.stop(vmid)
                    for vmid in set(data_in['vmids'])]
        return {
            'rc': max(rc for rc, msg in data_out),
            'msg': [msg for rc, msg in data_out if msg],
        }

    elif command == 'list':
        rc, tables = pbmc_manager.list()

        header = ('VMID', 'Status', 'Address', 'Port')
        keys = ('vmid', 'status', 'address', 'port')
        return {
            'rc': rc,
            'header': header,
            'rows': [
                [table.get(key, '?') for key in keys] for table in tables
            ]
        }

    elif command == 'show':
        rc, table = pbmc_manager.show(data_in['vmid'])

        return {
            'rc': rc,
            'header': ('Property', 'Value'),
            'rows': table,
        }

    else:
        return {
            'rc': 1,
            'msg': ['Unknown command'],
        }


def application():
    """pbmcd application entry point

    Initializes, serves and cleans up everything.
    """
    pbmc_manager = ProxmoxBMCManager()

    pbmc_manager.periodic()

    def kill_children(*args):
        pbmc_manager.periodic(shutdown=True)
        sys.exit(0)

    # SIGTERM does not seem to propagate to multiprocessing
    signal.signal(signal.SIGTERM, kill_children)

    try:
        main_loop(pbmc_manager, command_dispatcher)
    except KeyboardInterrupt:
        LOG.info('Got keyboard interrupt, exiting')
        pbmc_manager.periodic(shutdown=True)
    except Exception as ex:
        LOG.error(
            'Control server error: %(error)s', {'error': ex}
        )
        pbmc_manager.periodic(shutdown=True)
