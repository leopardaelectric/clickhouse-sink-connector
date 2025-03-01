import os
import platform
import time
import uuid
import xml.etree.ElementTree as xmltree

import testflows.settings as settings
from testflows._core.testtype import TestSubType
from testflows.asserts import error
from testflows.core import *
from testflows.core.name import basename, parentname


def current_cpu():
    """Return current cpu architecture."""
    return platform.processor()


def check_clickhouse_version(version):
    """Compare ClickHouse version."""

    def check(test):
        if getattr(test.context, "clickhouse_version", None) is None:
            return False

        version_list = version.translate({ord(i): None for i in "<>="}).split(".")
        clickhouse_version_list = test.context.clickhouse_version.split(".")

        index = None

        for i in clickhouse_version_list:
            if i.isnumeric():
                continue
            index = clickhouse_version_list.index(i)

        index = (
            min(len(version_list), len(clickhouse_version_list))
            if index is None
            else min(len(version_list), index)
        )

        version_list = [int(i) for i in version_list[0:index]]
        clickhouse_version_list = [int(i) for i in clickhouse_version_list[0:index]]

        if version.startswith("=="):
            return clickhouse_version_list == version_list
        elif version.startswith(">="):
            return clickhouse_version_list >= version_list
        elif version.startswith("<="):
            return clickhouse_version_list <= version_list
        elif version.startswith("="):
            return clickhouse_version_list == version_list
        elif version.startswith(">"):
            return clickhouse_version_list > version_list
        elif version.startswith("<"):
            return clickhouse_version_list < version_list
        else:
            return clickhouse_version_list == version_list

    return check


def getuid(with_test_name=False):
    if not with_test_name:
        return str(uuid.uuid1()).replace("-", "_")

    if current().subtype == TestSubType.Example:
        testname = (
            f"{basename(parentname(current().name)).replace(' ', '_').replace(',', '')}"
        )
    else:
        testname = f"{basename(current().name).replace(' ', '_').replace(',', '')}"

    return testname + "_" + str(uuid.uuid1()).replace("-", "_")


@TestStep(Given)
def instrument_clickhouse_server_log(
    self,
    node=None,
    test=None,
    clickhouse_server_log="/var/log/clickhouse-server/clickhouse-server.log",
    always_dump=False,
):
    """Instrument clickhouse-server.log for the current test (default)
    by adding start and end messages that include test name to log
    of the specified node. If we are in the debug mode and the test
    fails then dump the messages from the log for this test.

    :param always_dump: always dump clickhouse log after test, default: `False`
    """
    if test is None:
        test = current()

    if node is None:
        node = self.context.node

    with By("getting current log size"):
        cmd = node.command(f"stat --format=%s {clickhouse_server_log}")
        if (
            cmd.output
            == f"stat: cannot stat '{clickhouse_server_log}': No such file or directory"
        ):
            start_logsize = 0
        else:
            start_logsize = cmd.output.split(" ")[0].strip()

    try:
        with And("adding test name start message to the clickhouse-server.log"):
            node.command(
                f'echo -e "\\n-- start: {test.name} --\\n" >> {clickhouse_server_log}'
            )
        yield

    finally:
        if test.terminating is True:
            return

        with Finally(
            "adding test name end message to the clickhouse-server.log", flags=TE
        ):
            node.command(
                f'echo -e "\\n-- end: {test.name} --\\n" >> {clickhouse_server_log}'
            )

        with And("getting current log size at the end of the test"):
            cmd = node.command(f"stat --format=%s {clickhouse_server_log}")
            end_logsize = cmd.output.split(" ")[0].strip()

        dump_log = always_dump or (settings.debug and not self.parent.result)

        if dump_log:
            with Then("dumping clickhouse-server.log for this test"):
                node.command(
                    f"tail -c +{start_logsize} {clickhouse_server_log}"
                    f" | head -c {int(end_logsize) - int(start_logsize)}"
                )


xml_with_utf8 = '<?xml version="1.0" encoding="utf-8"?>\n'


def xml_indent(elem, level=0, by="  "):
    i = "\n" + level * by
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + by
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            xml_indent(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def xml_append(root, tag, text):
    element = xmltree.Element(tag)
    element.text = text
    root.append(element)
    return element


class Config:
    def __init__(self, content, path, name, uid, preprocessed_name):
        self.content = content
        self.path = path
        self.name = name
        self.uid = uid
        self.preprocessed_name = preprocessed_name


class KeyWithAttributes:
    def __init__(self, name, attributes):
        """XML key with attributes.

        :param name: key name
        :param attributes: dictionary of attributes {name: value, ...}
        """
        self.name = name
        self.attributes = dict(attributes)


def create_xml_config_content(
    entries, config_file, config_d_dir="/etc/clickhouse-server/config.d"
):
    """Create XML configuration file from a dictionary.

    :param entries: dictionary that defines xml
    :param config_file: name of the config file
    :param config_d_dir: config.d directory path, default: `/etc/clickhouse-server/config.d`
    """
    uid = getuid()
    path = os.path.join(config_d_dir, config_file)
    name = config_file
    root = xmltree.Element("yandex")
    root.append(xmltree.Comment(text=f"config uid: {uid}"))

    def create_xml_tree(entries, root):
        for k, v in entries.items():
            if isinstance(k, KeyWithAttributes):
                xml_element = xmltree.Element(k.name)
                for attr_name, attr_value in k.attributes.items():
                    xml_element.set(attr_name, attr_value)
                if type(v) is dict:
                    create_xml_tree(v, xml_element)
                elif type(v) in (list, tuple):
                    for e in v:
                        create_xml_tree(e, xml_element)
                else:
                    xml_element.text = v
                root.append(xml_element)
            elif type(v) is dict:
                xml_element = xmltree.Element(k)
                create_xml_tree(v, xml_element)
                root.append(xml_element)
            elif type(v) in (list, tuple):
                xml_element = xmltree.Element(k)
                for e in v:
                    create_xml_tree(e, xml_element)
                root.append(xml_element)
            else:
                xml_append(root, k, v)

    create_xml_tree(entries, root)
    xml_indent(root)
    content = xml_with_utf8 + str(
        xmltree.tostring(root, short_empty_elements=False, encoding="utf-8"), "utf-8"
    )

    return Config(content, path, name, uid, "config.xml")


def add_invalid_config(
    config, message, recover_config=None, tail=30, timeout=300, restart=True, user=None
):
    """Check that ClickHouse errors when trying to load invalid configuration file."""
    cluster = current().context.cluster
    node = current().context.node

    try:
        with Given("I prepare the error log by writing empty lines into it"):
            node.command(
                'echo -e "%s" > /var/log/clickhouse-server/clickhouse-server.err.log'
                % ("-\\n" * tail)
            )

        with When("I add the config", description=config.path):
            command = f"cat <<HEREDOC > {config.path}\n{config.content}\nHEREDOC"
            node.command(command, steps=False, exitcode=0)

        with Then(
            f"{config.preprocessed_name} should be updated",
            description=f"timeout {timeout}",
        ):
            started = time.time()
            command = f"cat /var/lib/clickhouse/preprocessed_configs/{config.preprocessed_name} | grep {config.uid}{' > /dev/null' if not settings.debug else ''}"
            while time.time() - started < timeout:
                exitcode = node.command(command, steps=False).exitcode
                if exitcode == 0:
                    break
                time.sleep(1)
            assert exitcode == 0, error()

        if restart:
            with When("I restart ClickHouse to apply the config changes"):
                node.restart_clickhouse(safe=False, wait_healthy=False, user=user)

    finally:
        if recover_config is None:
            with Finally(f"I remove {config.name}"):
                with By("removing invalid configuration file"):
                    system_config_path = os.path.join(
                        cluster.environ["CLICKHOUSE_TESTS_DIR"],
                        "configs",
                        node.name,
                        "config.d",
                        config.path.split("config.d/")[-1],
                    )
                    cluster.command(
                        None,
                        f"rm -rf {system_config_path}",
                        timeout=timeout,
                        exitcode=0,
                    )

                if restart:
                    with And("restarting ClickHouse"):
                        node.restart_clickhouse(safe=False, user=user)
                        node.restart_clickhouse(safe=False, user=user)
        else:
            with Finally(f"I change {config.name}"):
                with By("changing invalid configuration file"):
                    system_config_path = os.path.join(
                        cluster.environ["CLICKHOUSE_TESTS_DIR"],
                        "configs",
                        node.name,
                        "config.d",
                        config.path.split("config.d/")[-1],
                    )
                    cluster.command(
                        None,
                        f"rm -rf {system_config_path}",
                        timeout=timeout,
                        exitcode=0,
                    )
                    command = f"cat <<HEREDOC > {system_config_path}\n{recover_config.content}\nHEREDOC"
                    cluster.command(None, command, timeout=timeout, exitcode=0)

                if restart:
                    with And("restarting ClickHouse"):
                        node.restart_clickhouse(safe=False, user=user)

    with Then("error log should contain the expected error message"):
        started = time.time()
        command = f'tail -n {tail} /var/log/clickhouse-server/clickhouse-server.err.log | grep "{message}"'
        while time.time() - started < timeout:
            exitcode = node.command(command, steps=False).exitcode
            if exitcode == 0:
                break
            time.sleep(1)
        assert exitcode == 0, error()


def add_config(
    config,
    timeout=300,
    restart=False,
    modify=False,
    node=None,
    user=None,
    wait_healthy=True,
    check_preprocessed=True,
):
    """Add dynamic configuration file to ClickHouse.

    :param config: configuration file description
    :param timeout: timeout, default: 300 sec
    :param restart: restart server, default: False
    :param modify: only modify configuration file, default: False
    """
    if node is None:
        node = current().context.node
    cluster = current().context.cluster

    def check_preprocessed_config_is_updated(after_removal=False):
        """Check that preprocessed config is updated."""
        started = time.time()
        command = f"cat /var/lib/clickhouse/preprocessed_configs/{config.preprocessed_name} | grep {config.uid}{' > /dev/null' if not settings.debug else ''}"

        while time.time() - started < timeout:
            exitcode = node.command(command, steps=False).exitcode
            if after_removal:
                if exitcode == 1:
                    break
            else:
                if exitcode == 0:
                    break
            time.sleep(1)

        if settings.debug:
            node.command(
                f"cat /var/lib/clickhouse/preprocessed_configs/{config.preprocessed_name}"
            )

        if after_removal:
            assert exitcode == 1, error()
        else:
            assert exitcode == 0, error()

    def wait_for_config_to_be_loaded(user=None):
        """Wait for config to be loaded."""
        if restart:
            with When("I close terminal to the node to be restarted"):
                bash.close()

            with And("I stop ClickHouse to apply the config changes"):
                node.stop_clickhouse(safe=False)

            with And("I get the current log size"):
                cmd = node.cluster.command(
                    None,
                    f"stat --format=%s {cluster.environ['CLICKHOUSE_TESTS_DIR']}/_instances/{node.name}/logs/clickhouse-server.log",
                )
                logsize = cmd.output.split(" ")[0].strip()

            with And("I start ClickHouse back up"):
                node.start_clickhouse(user=user, wait_healthy=wait_healthy)

            with Then("I tail the log file from using previous log size as the offset"):
                bash.prompt = bash.__class__.prompt
                bash.open()
                bash.send(
                    f"tail -c +{logsize} -f /var/log/clickhouse-server/clickhouse-server.log"
                )

        with Then("I wait for config reload message in the log file"):
            if restart:
                choice = bash.expect(
                    (
                        f"(ConfigReloader: Loaded config '/etc/clickhouse-server/config.xml', performed update on configuration)|"
                        f"(ConfigReloader: Error updating configuration from '/etc/clickhouse-server/config.xml')"
                    ),
                    timeout=timeout,
                )
            else:
                choice = bash.expect(
                    (
                        f"(ConfigReloader: Loaded config '/etc/clickhouse-server/{config.preprocessed_name}', performed update on configuration)|"
                        f"(ConfigReloader: Error updating configuration from '/etc/clickhouse-server/{config.preprocessed_name}')"
                    ),
                    timeout=timeout,
                )
            if choice.group(2):
                bash.expect(".+\n", timeout=5, expect_timeout=True)
                fail("ConfigReloader: Error updating configuration")

    try:
        with Given(f"{config.name}"):
            if settings.debug:
                with When("I output the content of the config"):
                    debug(config.content)

            with node.cluster.shell(node.name) as bash:
                bash.expect(bash.prompt)
                bash.send(
                    "tail -v -n 0 -f /var/log/clickhouse-server/clickhouse-server.log"
                )
                # make sure tail process is launched and started to follow the file
                bash.expect("<==")
                bash.expect("\n")

                with When("I add the config", description=config.path):
                    command = (
                        f"cat <<HEREDOC > {config.path}\n{config.content}\nHEREDOC"
                    )
                    node.command(command, steps=False, exitcode=0)

                if check_preprocessed:
                    with Then(
                        f"{config.preprocessed_name} should be updated",
                        description=f"timeout {timeout}",
                    ):
                        check_preprocessed_config_is_updated()

                    with And("I wait for config to be reloaded"):
                        wait_for_config_to_be_loaded(user=user)

        yield
    finally:
        if not modify:
            with Finally(f"I remove {config.name} on {node.name}"):
                with node.cluster.shell(node.name) as bash:
                    bash.expect(bash.prompt)
                    bash.send(
                        "tail -v -n 0 -f /var/log/clickhouse-server/clickhouse-server.log"
                    )
                    # make sure tail process is launched and started to follow the file
                    bash.expect("<==")
                    bash.expect("\n")

                    with By("removing the config file", description=config.path):
                        node.command(f"rm -rf {config.path}", exitcode=0)

                    with Then(
                        f"{config.preprocessed_name} should be updated",
                        description=f"timeout {timeout}",
                    ):
                        check_preprocessed_config_is_updated(after_removal=True)

                    with And("I wait for config to be reloaded"):
                        wait_for_config_to_be_loaded()


@TestStep(Given)
def copy(
    self,
    dest_node,
    src_path,
    dest_path,
    bash=None,
    binary=False,
    eof="EOF",
    src_node=None,
):
    """Copy file from source to destination node."""
    if binary:
        raise NotImplementedError("not yet implemented; need to use base64 encoding")

    bash = self.context.cluster.bash(node=src_node)

    cmd = bash(f"cat {src_path}")

    assert cmd.exitcode == 0, error()
    contents = cmd.output
    try:
        dest_node.command(f"cat << {eof} > {dest_path}\n{contents}\n{eof}")
        yield dest_path
    finally:
        with Finally(f"I delete {dest_path}"):
            dest_node.command(f"rm -rf {dest_path}")


@TestStep(Given)
def add_user_to_group_on_node(
    self, node=None, group="clickhouse", username="clickhouse"
):
    """Add user {username} into group {group}."""
    if node is None:
        node = self.context.node

    node.command(f"usermode -g {group} {username}", exitcode=0)


@TestStep(Given)
def change_user_on_node(self, node=None, username="clickhouse"):
    """Change user on node."""
    if node is None:
        node = self.context.node
    try:
        node.command(f"su {username}", exitcode=0)
        yield
    finally:
        node.command("exit", exitcode=0)


@TestStep(Given)
def add_user_on_node(self, node=None, groupname=None, username="clickhouse"):
    """Create user on node with group specifying."""
    if node is None:
        node = self.context.node
    try:
        if groupname is None:
            node.command(f"useradd -s /bin/bash {username}", exitcode=0)
        else:
            node.command(f"useradd -g {groupname} -s /bin/bash {username}", exitcode=0)
        yield
    finally:
        node.command(f"deluser {username}", exitcode=0)


@TestStep(Given)
def add_group_on_node(self, node=None, groupname="clickhouse"):
    """Create group on node"""
    if node is None:
        node = self.context.node
    try:
        node.command(f"groupadd {groupname}", exitcode=0)
        yield
    finally:
        node.command(f"delgroup clickhouse")


@TestStep(Given)
def create_file_on_node(self, path, content, node=None):
    """Create file on node.

    :param path: file path
    :param content: file content
    """
    if node is None:
        node = self.context.node
    try:
        with By(f"creating file {path}"):
            node.command(f"cat <<HEREDOC > {path}\n{content}\nHEREDOC", exitcode=0)
        yield path
    finally:
        with Finally(f"I remove {path}"):
            node.command(f"rm -rf {path}", exitcode=0)


@TestStep(Given)
def set_envs_on_node(self, envs, node=None):
    """Set environment variables on node.

    :param envs: dictionary of env variables key=value
    """
    if node is None:
        node = self.context.node
    try:
        with By("setting envs"):
            for key, value in envs.items():
                node.command(f"export {key}={value}", exitcode=0)
        yield
    finally:
        with Finally(f"I unset envs"):
            for key in envs:
                node.command(f"unset {key}", exitcode=0)


from helpers.cluster import Cluster


@TestStep(Given)
def create_cluster(
    self,
    local=False,
    clickhouse_binary_path=None,
    clickhouse_odbc_bridge_binary_path=None,
    configs_dir=None,
    nodes=None,
    docker_compose="docker-compose",
    docker_compose_project_dir=None,
    docker_compose_file="docker-compose.yml",
    environ=None,
    thread_fuzzer=False,
    collect_service_logs=None,
    stress=None,
    caller_dir=None,
    frame=None,
):
    """Create docker-compose test environment cluster."""
    with Cluster(
        local=local,
        clickhouse_binary_path=clickhouse_binary_path,
        clickhouse_odbc_bridge_binary_path=clickhouse_odbc_bridge_binary_path,
        configs_dir=configs_dir,
        nodes=nodes,
        docker_compose=docker_compose,
        docker_compose_project_dir=docker_compose_project_dir,
        docker_compose_file=docker_compose_file,
        environ=environ,
        thread_fuzzer=thread_fuzzer,
        collect_service_logs=collect_service_logs,
        stress=stress,
        frame=frame,
        caller_dir=caller_dir,
    ) as cluster:
        yield cluster
