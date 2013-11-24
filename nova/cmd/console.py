# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2010 OpenStack Foundation
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

"""Starter script for Nova Console Proxy."""

import sys

from oslo.config import cfg

from nova import config
from nova.openstack.common import log as logging
from nova import service

CONF = cfg.CONF
CONF.import_opt('console_topic', 'nova.console.rpcapi')
前者の引数が任意設定、後者がデフォルト


def main():nova/cmd/console.pyのmainが一番最初。C言語のmain文と同じ。
    config.parse_args(sys.argv)
    コンフィグファイルから漏れた設定をコマンドライン引数から読み込んでる
    logging.setup("nova")
    server = service.Service.create(binary='nova-console',
                                    topic=CONF.console_topic)
    topicはRPCの中での相手を意味してる。
    service.serve(server)
    service.wait()
    待ちに入る。イベントドリブン。
