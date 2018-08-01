import sys
import os
import pytest
import time

from datetime import datetime, timedelta
from insights.client import InsightsClient
from insights.client.archive import InsightsArchive
from insights.client.config import InsightsConfig
from insights.client.client import _delete_archive_internal
from insights import package_info
from insights.client.constants import InsightsConstants as constants
from insights.client.connection import InsightsConnection
from insights.client.utilities import generate_machine_id
from mock.mock import call, Mock, patch
from os.path import lexists


class CurrentTimeMatcher():
    """
    Matches a ISO timestamp if it is now or a little before. Used to check that something didn't happen eons ago.
    """

    def __init__(self):
        self.now = datetime.now()

    def __eq__(self, other_str):
        # *** Could be done in a less cryptic way by using dateutils library.
        other_ts = datetime.strptime(other_str, '%Y-%m-%dT%H:%M:%S.%f')

        delta = self.now - other_ts
        return delta < timedelta(seconds=5)  # *** Assume that the tests as a whole don't run longer than 5 minutes.


class FakeConnection(object):
    '''
    For stubbing out network calls
    '''
    def __init__(self, registered=None):
        self.registered = registered

    def api_registration_check(self):
        # True = registered
        # None or string = unregistered
        # False = unreachable
        return self.registered

    def register(self):
        return ('msg', 'hostname', "None", "")

    def unregister(self):
        return True


# @TODO DRY the args hack.

def test_version():

    # Hack to prevent client from parsing args to py.test
    tmp = sys.argv
    sys.argv = []

    try:
        config = InsightsConfig(logging_file='/tmp/insights.log')
        client = InsightsClient(config)
        result = client.version()
        assert result == "%s-%s" % (package_info["VERSION"], package_info["RELEASE"])
    finally:
        sys.argv = tmp


def register_lexists(filename):
    """
    Mocks a situation, when a machine is unregistered: there is an .unregistered file, but no .registered one.
    """
    # *** I don't like much configuring the mock at two places at once: inside the test and in the patch call. Thus
    #     putting this method outside to pass it to patch then.
    if filename in constants.registered_files:
        return False
    elif filename in constants.unregistered_files:
        return True
    else:
        return lexists(filename)


@patch("insights.client.utilities.os.path.lexists", register_lexists)
@patch("insights.client.utilities.open")
@patch("insights.client.utilities.os.remove")
def test_register(os_remove, builtin_open):
    config = InsightsConfig(register=True)
    client = InsightsClient(config)
    client.connection = Mock(**{"api_registration_check.return_value": None,
                                "register.return_value": ("msg", "hostname", "None", "")})
    client.session = True

    # *** I'd personally put both of these checks into another test. Also removed "== True".
    assert client.register()
    client.connection.register.assert_called_once()

    open_calls = []
    write_calls = []
    for r in constants.registered_files:
        open_call = call(r, "wb")
        open_calls.append(open_call)

        write_call = call(CurrentTimeMatcher())
        write_calls.append(write_call)

    # *** The any_order is required to ignore the calls in between.
    builtin_open.assert_has_calls(open_calls, any_order=True)
    # *** Unfortunately it's not possible to patch the default contents, because get_time is evaluated at function
    #     definition time and not at its call time. Let's at least test it's earlier and close enough. The way its
    #     written is rather ugly, but that's just how it works.
    builtin_open.return_value.__enter__.return_value.write.assert_has_calls(write_calls, any_order=True)

    remove_calls = []
    for u in constants.unregistered_files:
        remove_call = call(u)
        remove_calls.append(remove_call)
    os_remove.assert_has_calls(remove_calls, any_order=True)


@pytest.mark.skip(reason="Mocked paths not working in QE jenkins")
@patch('insights.client.utilities.constants.registered_files',
       ['/tmp/insights-client.registered',
        '/tmp/redhat-access-insights.registered'])
@patch('insights.client.utilities.constants.unregistered_files',
       ['/tmp/insights-client.unregistered',
        '/tmp/redhat-access-insights.unregistered'])
@patch('insights.client.utilities.constants.machine_id_file',
       '/tmp/machine-id')
def test_unregister():
    config = InsightsConfig(unregister=True)
    client = InsightsClient(config)
    client.connection = FakeConnection(registered=True)
    client.session = True
    assert client.unregister() is True
    for r in constants.registered_files:
        assert os.path.isfile(r) is False
    for u in constants.unregistered_files:
        assert os.path.isfile(u) is True


@pytest.mark.skip(reason="Mocked paths not working in QE jenkins")
@patch('insights.client.utilities.constants.registered_files',
       ['/tmp/insights-client.registered',
        '/tmp/redhat-access-insights.registered'])
@patch('insights.client.utilities.constants.unregistered_files',
       ['/tmp/insights-client.unregistered',
        '/tmp/redhat-access-insights.unregistered'])
@patch('insights.client.utilities.constants.machine_id_file',
       '/tmp/machine-id')
def test_force_reregister():
    config = InsightsConfig(reregister=True)
    client = InsightsClient(config)
    client.connection = FakeConnection(registered=None)
    client.session = True

    # initialize comparisons
    old_machine_id = None
    new_machine_id = None

    # register first
    assert client.register() is True
    for r in constants.registered_files:
        assert os.path.isfile(r) is True

    # get modified time of .registered to ensure it's regenerated
    old_reg_file1_ts = os.path.getmtime(constants.registered_files[0])
    old_reg_file2_ts = os.path.getmtime(constants.registered_files[1])

    old_machine_id = generate_machine_id()

    # wait to allow for timestamp difference
    time.sleep(3)

    # reregister with new machine-id
    client.connection = FakeConnection(registered=True)
    config.reregister = True
    assert client.register() is True

    new_machine_id = generate_machine_id()
    new_reg_file1_ts = os.path.getmtime(constants.registered_files[0])
    new_reg_file2_ts = os.path.getmtime(constants.registered_files[1])

    assert old_machine_id != new_machine_id
    assert old_reg_file1_ts != new_reg_file1_ts
    assert old_reg_file2_ts != new_reg_file2_ts


def test_register_container():
    with pytest.raises(ValueError):
        InsightsConfig(register=True, analyze_container=True)


def test_unregister_container():
    with pytest.raises(ValueError):
        InsightsConfig(unregister=True, analyze_container=True)


def test_force_reregister_container():
    with pytest.raises(ValueError):
        InsightsConfig(reregister=True, analyze_container=True)


@pytest.mark.skip(reason="Mocked paths not working in QE jenkins")
@patch('insights.client.utilities.constants.registered_files',
       ['/tmp/insights-client.registered',
        '/tmp/redhat-access-insights.registered'])
@patch('insights.client.utilities.constants.unregistered_files',
       ['/tmp/insights-client.unregistered',
        '/tmp/redhat-access-insights.unregistered'])
@patch('insights.client.utilities.constants.machine_id_file',
       '/tmp/machine-id')
def test_reg_check_registered():
    # register the machine first
    config = InsightsConfig()
    client = InsightsClient(config)
    client.connection = FakeConnection(registered=True)
    client.session = True

    # test function and integration in .register()
    assert client.get_registation_status()['status'] is True
    assert client.register() is True
    for r in constants.registered_files:
        assert os.path.isfile(r) is True
    for u in constants.unregistered_files:
        assert os.path.isfile(u) is False


@pytest.mark.skip(reason="Mocked paths not working in QE jenkins")
@patch('insights.client.utilities.constants.registered_files',
       ['/tmp/insights-client.registered',
        '/tmp/redhat-access-insights.registered'])
@patch('insights.client.utilities.constants.unregistered_files',
       ['/tmp/insights-client.unregistered',
        '/tmp/redhat-access-insights.unregistered'])
@patch('insights.client.utilities.constants.machine_id_file',
       '/tmp/machine-id')
def test_reg_check_unregistered():
    # unregister the machine first
    config = InsightsConfig()
    client = InsightsClient(config)
    client.connection = FakeConnection(registered='unregistered')
    client.session = True

    # test function and integration in .register()
    assert client.get_registation_status()['status'] is False
    assert client.register() is False
    for r in constants.registered_files:
        assert os.path.isfile(r) is False
    for u in constants.unregistered_files:
        assert os.path.isfile(u) is True


@pytest.mark.skip(reason="Mocked paths not working in QE jenkins")
@patch('insights.client.utilities.constants.registered_files',
       ['/tmp/insights-client.registered',
        '/tmp/redhat-access-insights.registered'])
@patch('insights.client.utilities.constants.unregistered_files',
       ['/tmp/insights-client.unregistered',
        '/tmp/redhat-access-insights.unregistered'])
@patch('insights.client.utilities.constants.machine_id_file',
       '/tmp/machine-id')
def test_reg_check_registered_unreachable():
    # register the machine first
    config = InsightsConfig(register=True)
    client = InsightsClient(config)
    client.connection = FakeConnection(registered=None)
    client.session = True
    assert client.register() is True

    # reset config and try to check registration
    config.register = False
    client.connection = FakeConnection(registered=False)
    assert client.get_registation_status()['unreachable'] is True
    assert client.register() is None
    for r in constants.registered_files:
        assert os.path.isfile(r) is True
    for u in constants.unregistered_files:
        assert os.path.isfile(u) is False


@pytest.mark.skip(reason="Mocked paths not working in QE jenkins")
@patch('insights.client.utilities.constants.registered_files',
       ['/tmp/insights-client.registered',
        '/tmp/redhat-access-insights.registered'])
@patch('insights.client.utilities.constants.unregistered_files',
       ['/tmp/insights-client.unregistered',
        '/tmp/redhat-access-insights.unregistered'])
@patch('insights.client.utilities.constants.machine_id_file',
       '/tmp/machine-id')
def test_reg_check_unregistered_unreachable():
    # unregister the machine first
    config = InsightsConfig(unregister=True)
    client = InsightsClient(config)
    client.connection = FakeConnection(registered=True)
    client.session = True
    assert client.unregister() is True

    # reset config and try to check registration
    config.unregister = False
    client.connection = FakeConnection(registered=False)
    assert client.get_registation_status()['unreachable'] is True
    assert client.register() is None
    for r in constants.registered_files:
        assert os.path.isfile(r) is False
    for u in constants.unregistered_files:
        assert os.path.isfile(u) is True


@patch('insights.client.client.constants.sleep_time', 0)
@patch('insights.client.client.InsightsConnection.upload_archive',
       return_value=Mock(status_code=500))
def test_upload_500_retry(upload_archive):

    # Hack to prevent client from parsing args to py.test
    tmp = sys.argv
    sys.argv = []

    try:
        retries = 3

        config = InsightsConfig(logging_file='/tmp/insights.log', retries=retries)
        client = InsightsClient(config)
        client.upload('/tmp/insights.tar.gz')

        upload_archive.assert_called()
        assert upload_archive.call_count == retries
    finally:
        sys.argv = tmp


@patch('insights.client.client.InsightsConnection.handle_fail_rcs')
@patch('insights.client.client.InsightsConnection.upload_archive',
       return_value=Mock(status_code=412))
def test_upload_412_no_retry(upload_archive, handle_fail_rcs):

    # Hack to prevent client from parsing args to py.test
    tmp = sys.argv
    sys.argv = []

    try:
        config = InsightsConfig(logging_file='/tmp/insights.log', retries=3)
        client = InsightsClient(config)
        client.upload('/tmp/insights.tar.gz')

        upload_archive.assert_called_once()
    finally:
        sys.argv = tmp


@patch('insights.client.connection.write_unregistered_file')
@patch('insights.client.client.InsightsConnection.upload_archive',
       return_value=Mock(**{"status_code": 412,
                            "json.return_value": {"unregistered_at": "now", "message": "msg"}}))
def test_upload_412_write_unregistered_file(upload_archive, write_unregistered_file):

    # Hack to prevent client from parsing args to py.test
    tmp = sys.argv
    sys.argv = []

    try:
        config = InsightsConfig(logging_file='/tmp/insights.log', retries=3)
        client = InsightsClient(config)
        client.upload('/tmp/insights.tar.gz')

        unregistered_at = upload_archive.return_value.json()["unregistered_at"]
        write_unregistered_file.assert_called_once_with(unregistered_at)
    finally:
        sys.argv = tmp


def test_delete_archive_internal():
    config = InsightsConfig(keep_archive=True)
    arch = InsightsArchive()
    _delete_archive_internal(config, arch)
    assert os.path.exists(arch.tmp_dir)
    assert os.path.exists(arch.archive_tmp_dir)

    config.keep_archive = False
    _delete_archive_internal(config, arch)
    assert not os.path.exists(arch.tmp_dir)
    assert not os.path.exists(arch.archive_tmp_dir)
