# container-service-extension
# Copyright (c) 2017 VMware, Inc. All Rights Reserved.
# SPDX-License-Identifier: BSD-2-Clause

from __future__ import print_function
import click
import hashlib
import logging
import os
import pika
from pyvcloud.vcd.client import BasicLoginCredentials
from pyvcloud.vcd.client import Client
from pyvcloud.vcd.client import QueryResultFormat
from pyvcloud.vcd.client import SIZE_1MB
from pyvcloud.vcd.org import Org
from pyvcloud.vcd.vapp import VApp
from pyvcloud.vcd.vdc import VDC
from pyvcloud.vcd.vsphere import VSphere
import requests
import time
from vcd_cli.utils import stdout
import yaml


LOGGER = logging.getLogger(__name__)
BUF_SIZE = 65536


def generate_sample_config():
    sample_config = """
amqp:
    host: amqp.vmware.com
    port: 5672
    user: 'guest'
    password: 'guest'
    exchange: vcdext
    routing_key: cse

vcd:
    host: vcd.vmware.com
    port: 443
    username: 'administrator'
    password: 'my_secret_password'
    api_version: '6.0'
    verify: False
    log: True

vcs:
    host: vcenter.vmware.com
    port: 443
    username: 'administrator@vsphere.local'
    password: 'my_secret_password'
    verify: False

service:
    listeners: 2
    logging_level: 5
    logging_format: {logging_format}

broker:
    type: default
    org: org1
    vdc: vdc1
    catalog: cse
    source_ova: https://bintray.com/vmware/photon/download_file?file_path=photon-custom-hw11-1.0-62c543d.ova
    sha1_ova: 18c1a6d31545b757d897c61a0c3cc0e54d8aeeba
    master_template: k8s-u.ova
    node_template: k8s-u.ova
    password: 'my_secret_password'
    ssh_public_key: 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDFS5HL4CBlWrZscohhqdVwUa815Pi3NaCijfdvs0xCNF2oP458Xb3qYdEmuFWgtl3kEM4hR60/Tzk7qr3dmAfY7GPqdGhQsZEnvUJq0bfDAh0KqhdrqiIqx9zlKWnR65gl/u7Qkck2jiKkqjfxZwmJcuVCu+zQZCRC80XKwpyOudLKd/zJz9tzJxJ7+yltu9rNdshCEfP+OR1QoY2hFRH1qaDHTIbDdlF/m0FavapH7+ScufOY/HNSSYH7/SchsxK3zywOwGV1e1z//HHYaj19A3UiNdOqLkitKxFQrtSyDfClZ/0SwaVxh4jqrKuJ5NT1fbN2bpDWMgffzD9WWWZbDvtYQnl+dBjDnzBZGo8miJ87lYiYH9N9kQfxXkkyPziAjWj8KZ8bYQWJrEQennFzsbbreE8NtjsM059RXz0kRGeKs82rHf0mTZltokAHjoO5GmBZb8sZTdZyjfo0PTgaNCENe0brDTrAomM99LhW2sJ5ZjK7SIqpWFaU+P+qgj4s88btCPGSqnh0Fea1foSo5G57l5YvfYpJalW0IeiynrO7TRuxEVV58DJNbYyMCvcZutuyvNq0OpEQYXRM2vMLQX3ZX3YhHMTlSXXcriqvhOJ7aoNae5aiPSlXvgFi/wP1x1aGYMEsiqrjNnrflGk9pIqniXsJ/9TFwRh9m4GktQ== cse'
    master_cpu: 2
    master_mem: 2048
    node_cpu: 2
    node_mem: 2048

    """.format(logging_format='%(levelname) -8s %(asctime)s %(name) -40s %(funcName) -35s %(lineno) -5d: %(message)s')  # NOQA
    return sample_config.strip() + '\n'


def bool_to_msg(value):
    if value:
        return 'success'
    else:
        return 'fail'


def get_config(file_name):
    config = {}
    with open(file_name, 'r') as f:
        config = yaml.load(f)
    if not config['vcd']['verify']:
        click.secho('InsecureRequestWarning: '
                    'Unverified HTTPS request is being made. '
                    'Adding certificate verification is strongly '
                    'advised.', fg='yellow', err=True)
        requests.packages.urllib3.disable_warnings()
    return config


def check_config(file_name):
    config = get_config(file_name)
    amqp = config['amqp']
    credentials = pika.PlainCredentials(amqp['user'], amqp['password'])
    parameters = pika.ConnectionParameters(amqp['host'], amqp['port'],
                                           '/',
                                           credentials)
    connection = pika.BlockingConnection(parameters)
    click.echo('Connected to AMQP server (%s:%s): %s' % (amqp['host'],
               amqp['port'],
               bool_to_msg(connection.is_open)))
    connection.close()
    if not config['vcd']['verify']:
        click.secho('InsecureRequestWarning: '
                    'Unverified HTTPS request is being made. '
                    'Adding certificate verification is strongly '
                    'advised.', fg='yellow', err=True)
        requests.packages.urllib3.disable_warnings()
    client = Client(config['vcd']['host'],
                    api_version=config['vcd']['api_version'],
                    verify_ssl_certs=config['vcd']['verify'],
                    log_file='cse.log',
                    log_headers=True,
                    log_bodies=True
                    )
    client.set_credentials(BasicLoginCredentials(config['vcd']['username'],
                                                 'System',
                                                 config['vcd']['password']))
    click.echo('Connected to vCloud Director as system '
               'administrator (%s:%s): %s' %
               (config['vcd']['host'], config['vcd']['port'],
                bool_to_msg(True)))

    if config['broker']['type'] == 'default':
        logged_in_org = client.get_org()
        org = Org(client, resource=logged_in_org)
        org.get_catalog(config['broker']['catalog'])
        click.echo('Find catalog \'%s\': %s' %
                   (config['broker']['catalog'], bool_to_msg(True)))
        org.get_catalog_item(config['broker']['catalog'],
                             config['broker']['master_template'])
        click.echo('Find master template \'%s\': %s' %
                   (config['broker']['master_template'], bool_to_msg(True)))
        org.get_catalog_item(config['broker']['catalog'],
                             config['broker']['node_template'])
        click.echo('Find node template \'%s\': %s' %
                   (config['broker']['node_template'], bool_to_msg(True)))

    v = VSphere(config['vcs']['host'],
                config['vcs']['username'],
                config['vcs']['password'],
                port=int(config['vcs']['port']))
    v.connect()
    click.echo('Connected to vCenter Server as %s '
               '(%s:%s): %s' %
               (config['vcs']['username'],
                config['vcs']['host'],
                config['vcs']['port'],
                bool_to_msg(True)))
    return config


def configure_vcd(ctx, file_name):
    click.secho('Configuring vCD from file: %s' % file_name)
    config = get_config(file_name)
    client = Client(config['vcd']['host'],
                    api_version=config['vcd']['api_version'],
                    verify_ssl_certs=config['vcd']['verify'],
                    log_file='cse.log',
                    log_headers=True,
                    log_bodies=True
                    )
    client.set_credentials(BasicLoginCredentials(config['vcd']['username'],
                                                 'System',
                                                 config['vcd']['password']))
    click.echo('Connected to vCloud Director as system '
               'administrator (%s:%s): %s' %
               (config['vcd']['host'], config['vcd']['port'],
                bool_to_msg(True)))
    if config['broker']['type'] == 'default':
        orgs = client.get_org_list()
        for org in [o for o in orgs.Org if hasattr(orgs, 'Org')]:
            if org.get('name') == config['broker']['org']:
                org_href = org.get('href')
        org = Org(client, href=org_href)
        click.echo('Find org \'%s\': %s' %
                   (org.get_name(), bool_to_msg(True)))
        vdc_resource = org.get_vdc(config['broker']['vdc'])
        click.echo('Find vdc \'%s\': %s' %
                   (vdc_resource.get('name'), bool_to_msg(True)))
        try:
            catalog = org.get_catalog(config['broker']['catalog'])
        except Exception:
            catalog = org.create_catalog(config['broker']['catalog'],
                                         'CSE catalog')
            org.share_catalog(config['broker']['catalog'])
            catalog = org.get_catalog(config['broker']['catalog'])
        click.echo('Find catalog \'%s\': %s' %
                   (config['broker']['catalog'],
                    bool_to_msg(catalog is not None)))
        master_template = None
        try:
            master_template = org.get_catalog_item(
                config['broker']['catalog'],
                config['broker']['master_template'])
        except Exception:
            create_master_template(
                ctx,
                config,
                client,
                org,
                vdc_resource,
                catalog)
        try:
            master_template = org.get_catalog_item(
                config['broker']['catalog'],
                config['broker']['master_template'])
        except Exception:
            pass
        click.echo('Find master template \'%s\': %s' %
                   (config['broker']['master_template'],
                    bool_to_msg(master_template is not None)))
        configure_amqp_settings(client, config)
        register_extension(client, config)


def get_sha1(file):
    sha1 = hashlib.sha1()
    with open(file, 'rb') as f:
        while True:
            data = f.read(BUF_SIZE)
            if not data:
                break
            sha1.update(data)
    return sha1.hexdigest()


def upload_source_ova(config, client, org, catalog):
    cse_cache_dir = os.path.join(os.getcwd(), 'cse_cache')
    cse_ova_file = os.path.join(cse_cache_dir,
                                config['broker']['source_ova_name'])
    if not os.path.exists(cse_ova_file):
        if not os.path.isdir(cse_cache_dir):
            os.makedirs(cse_cache_dir)
        click.secho('Downloading %s' % config['broker']['source_ova'],
                    fg='green')
        r = requests.get(config['broker']['source_ova'], stream=True)
        with open(cse_ova_file, 'wb') as fd:
            for chunk in r.iter_content(chunk_size=SIZE_1MB):
                fd.write(chunk)
    if os.path.exists(cse_ova_file):
        sha1 = get_sha1(cse_ova_file)
        assert sha1 == config['broker']['sha1_ova']
        click.secho('Uploading %s' % config['broker']['source_ova_name'],
                    fg='green')
        org.upload_ovf(
            config['broker']['catalog'],
            cse_ova_file,
            config['broker']['source_ova_name'],
            callback=None)
        return org.get_catalog_item(config['broker']['catalog'],
                                    config['broker']['source_ova_name'])
    else:
        return None


def create_master_template(ctx, config, client, org, vdc_resource, catalog):
    vapp_name = config['broker']['temp_vapp']
    ctx.obj = {}
    ctx.obj['client'] = client
    try:
        source_ova_item = org.get_catalog_item(
            config['broker']['catalog'],
            config['broker']['source_ova_name'])
    except Exception:
        source_ova_item = upload_source_ova(config, client, org, catalog)
    click.secho('Find source ova \'%s\': %s' %
                (config['broker']['source_ova_name'],
                 bool_to_msg(source_ova_item is not None)))
    if source_ova_item is None:
        return None
    item_id = source_ova_item.get('id')
    flag = False
    while True:
        q = client.get_typed_query(
                'adminCatalogItem',
                query_result_format=QueryResultFormat.ID_RECORDS,
                qfilter='id==%s' % item_id)
        records = list(q.execute())
        if records[0].get('status') == 'RESOLVED':
            if flag:
                click.secho('done', fg='blue')
            break
        else:
            if flag:
                click.secho('.', nl=False, fg='green')
            else:
                click.secho('Waiting for upload to complete...',
                            nl=False,
                            fg='green')
                flag = True
            time.sleep(5)
    vdc = VDC(client, resource=vdc_resource)
    try:
        vapp_resource = vdc.get_vapp(vapp_name)
    except Exception:
        vapp_resource = None
    if vapp_resource is not None:
        click.secho('Found vApp %s, capture as template' % vapp_name)
        return capture_as_template(ctx, config, vapp_resource, org, catalog)
    connection_mode = 'dhcp'
    vm_name = vapp_name
    cust_script = """
#!/bin/bash
/usr/bin/cp /etc/pam.d/sshd /etc/pam.d/vmtoolsd
temp="use-autogenerated"
echo -e "$temp\n$temp" | passwd root
chage -I -1 -m 0 -M -1 -E -1 root
        """
    vapp_resource = vdc.instantiate_vapp(
        vm_name,
        catalog.get('name'),
        config['broker']['source_ova_name'],
        network=config['broker']['network'],
        deploy=False,
        power_on=False,
        password=None,
        cust_script=cust_script)
    stdout(vapp_resource.Tasks.Task[0], ctx)
    vapp = VApp(client, href=vapp_resource.get('href'))
    task = vapp.connect_vm(mode=connection_mode)
    stdout(task, ctx)
    task = vapp.power_on()
    stdout(task, ctx)
    ip = None
    password_auto = None
    vm_moid = None
    click.secho('Waiting for IP address... ', nl=False, fg='green')
    while True:
        time.sleep(5)
        vapp = VApp(client, href=vapp_resource.get('href'))
        try:
            ip = vapp.get_primary_ip(vm_name)
            password_auto = vapp.get_admin_password(vm_name)
            vm_moid = vapp.get_vm_moid(vm_name)
            if ip is not None and \
               password_auto is not None and \
               vm_moid is not None:
                break
        except Exception:
            pass
    click.secho(ip, fg='blue')
    click.secho('Customizing template, please wait...', nl=False, fg='green')
    cust_script = """
#!/bin/bash
/bin/echo '{ssh_public_key}' >> $HOME/.ssh/authorized_keys
/bin/chmod go-rwx $HOME/.ssh/authorized_keys

/usr/bin/cat << EOF > /etc/systemd/system/iptables-ports.service
[Unit]
After=iptables.service
Requires=iptables.service
[Service]
Type=oneshot
ExecStartPre=/usr/sbin/iptables -P INPUT ACCEPT
ExecStartPre=/usr/sbin/iptables -P OUTPUT ACCEPT
ExecStart=/usr/sbin/iptables -P FORWARD ACCEPT
TimeoutSec=0
RemainAfterExit=yes
[Install]
WantedBy=iptables.service
EOF

/usr/bin/chmod 766 /etc/systemd/system/iptables-ports.service
/usr/bin/systemctl enable iptables-ports.service
/usr/bin/systemctl start iptables-ports.service

/usr/bin/systemctl enable docker.service
/usr/bin/systemctl start docker.service
/usr/bin/tdnf install -y kubernetes-1.7.5-1.ph1 kubernetes-kubeadm-1.7.5-1.ph1
/usr/bin/tdnf install -y wget

/usr/bin/docker pull gcr.io/google_containers/kube-controller-manager-amd64:v1.7.6
/usr/bin/docker pull gcr.io/google_containers/kube-scheduler-amd64:v1.7.6
/usr/bin/docker pull gcr.io/google_containers/kube-apiserver-amd64:v1.7.6
/usr/bin/docker pull gcr.io/google_containers/kube-proxy-amd64:v1.7.6
/usr/bin/docker pull gcr.io/google_containers/k8s-dns-sidecar-amd64:1.14.4
/usr/bin/docker pull gcr.io/google_containers/k8s-dns-kube-dns-amd64:1.14.4
/usr/bin/docker pull gcr.io/google_containers/k8s-dns-dnsmasq-nanny-amd64:1.14.4
/usr/bin/docker pull gcr.io/google_containers/etcd-amd64:3.0.17
/usr/bin/docker pull gcr.io/google_containers/pause-amd64:3.0
/usr/bin/docker pull quay.io/coreos/flannel:v0.9.0-amd64

/usr/bin/wget https://raw.githubusercontent.com/coreos/flannel/v0.9.0/Documentation/kube-flannel.yml

/bin/echo -n > /etc/machine-id
/bin/sync
/bin/sync
""".format(ssh_public_key=config['broker']['ssh_public_key'])  # NOQA
    vs = VSphere(config['vcs']['host'],
                 config['vcs']['username'],
                 config['vcs']['password'],
                 port=int(config['vcs']['port']))
    vs.connect()
    vm = vs.get_vm_by_moid(vm_moid)
    vs.upload_file_to_guest(
        vm,
        'root',
        password_auto,
        cust_script,
        '/tmp/customize.sh')
    click.secho('.', nl=False, fg='green')
    vs.execute_program_in_guest(
        vm,
        'root',
        password_auto,
        '/usr/bin/chmod',
        'u+rx /tmp/customize.sh',
        wait_for_completion=True)
    click.secho('.', nl=False, fg='green')
    vs.execute_program_in_guest(
        vm,
        'root',
        password_auto,
        '/tmp/customize.sh',
        '> /tmp/customize.out 2>&1',
        wait_for_completion=True)
    click.secho('.', nl=False, fg='green')
    vs.execute_program_in_guest(
        vm,
        'root',
        password_auto,
        '/usr/bin/rm',
        '-f /tmp/customize.sh',
        wait_for_completion=True)
    click.secho('.', nl=False, fg='green')
    click.secho('done', fg='blue')
    return capture_as_template(ctx, config, vapp_resource, org, catalog)


def capture_as_template(ctx, config, vapp_resource, org, catalog):
    client = ctx.obj['client']
    vapp = VApp(client, href=vapp_resource.get('href'))
    vapp.reload()
    if vapp.resource.get('status') == '4':
        task = vapp.shutdown()
        stdout(task, ctx)
    time.sleep(4)
    task = org.capture_vapp(
        catalog,
        vapp_resource.get('href'),
        config['broker']['master_template'],
        'CSE master template',
        customize_on_instantiate=True)
    stdout(task, ctx)
    return True


def configure_amqp_settings(client, config):
    click.secho('See https://vmware.github.io/container-service-extension to configure AMQP settings.')  # NOQA


def register_extension(client, config):
    click.secho('See https://vmware.github.io/container-service-extension to register API extension.')  # NOQA
