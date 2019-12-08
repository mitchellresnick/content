import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *
from typing import Dict, Tuple

import requests

# Disable insecure warnings
requests.packages.urllib3.disable_warnings()

''' GLOBALS/PARAMS '''
BPA_HOST = 'https://bpa.paloaltonetworks.com'
BPA_VERSION = 'v1'
BPA_URL = BPA_HOST + '/api/' + BPA_VERSION + '/'


class LightPanoramaClient(BaseClient):
    def __init__(self, server, port, api_key, verify, proxy):
        super().__init__(server.rstrip('/:') + ':' + port + '/', verify, proxy)
        self.api_key = api_key

    def simple_op_request(self, cmd):
        params = {
            'type': 'op',
            'cmd': cmd,
            'key': self.api_key
        }

        result = self._http_request(
            'POST',
            'api',
            params=params,
            resp_type='text'
        )

        return result

    @logger
    def get_system_time(self):
        return self.simple_op_request('<show><clock></clock></show>')

    @logger
    def get_license(self):
        return self.simple_op_request('<request><license><info></info></license></request>')

    @logger
    def get_system_info(self):
        return self.simple_op_request('<show><system><info></info></system></show>')

    @logger
    def get_running_config(self):
        params = {
            'type': 'config',
            'action': 'show',
            'key': self.api_key
        }

        result = self._http_request(
            'POST',
            'api',
            params=params,
            resp_type='text'
        )

        return result


class Client(BaseClient):
    """
    Client to use in the BPA integration. Overrides BaseClient
    """

    def __init__(self, bpa_token: str, verify: bool, proxy: bool):
        headers = {'Authorization': f'Token {bpa_token}'}
        super().__init__(base_url=BPA_URL, verify=verify, proxy=proxy, headers=headers)
        self.token = bpa_token

    def get_documentation_request(self):
        response = self._http_request('GET', 'documentation/')
        return response

    def submit_task_request(self, running_config, system_info, license_info, system_time) -> Dict:
        data = {
            'xml': running_config,
            'system_info': system_info,
            'license_info': license_info,
            'system_time': system_time
        }

        response = self._http_request('POST', 'create/', data=data)
        return response

    def get_results_request(self, task_id: str):
        response = self._http_request('GET', f'results/{task_id}/')
        return response


def filter_doc(doc: Dict) -> Dict:
    clone = doc.copy()
    del clone['complexity']
    del clone['effort']
    return clone


def get_documentation_command(client: Client) -> Tuple[str, Dict, Dict]:
    raw = client.get_documentation_request()
    if not raw:
        raise Exception('Failed getting documentation from BPA')
    filtered_docs = [filter_doc(doc) for doc in raw]

    entry_context = {'PAN-OS-BPA.Documentation': filtered_docs}
    human_readable = tableToMarkdown('BPA documentation', filtered_docs)

    return human_readable, entry_context, raw


def submit_task_command(client: Client, panorama: LightPanoramaClient) -> Tuple[str, Dict, Dict]:
    try:
        running_config = panorama.get_running_config()
        system_info = panorama.get_system_info()
        license_info = panorama.get_license()
        system_time = panorama.get_system_time()
    except:
        raise Exception('Failed getting response from Panorama.')

    raw = client.submit_task_request(running_config, system_info, license_info, system_time)
    task_id = raw.get('task_id', '')

    human_readable = f'Submitted BPA job ID: {task_id}'
    entry_context = {'PAN-OS-BPA.SubmittedJob(val.JobID && val.JobID === obj.JobID)': {'JobID': task_id}}

    return human_readable, entry_context, raw


def transform_check(check, feature, category):
    # safe to shallow clone since it is a shallow object
    transformed_check = check.copy()
    transformed_check['check_category'] = category
    transformed_check['check_feature'] = feature
    return transformed_check


def get_checks_from_feature(feature, feature_name, category):
    notes_checks = feature.get('notes', [])
    warnings_checks = feature.get('warnings', [])
    return [transform_check(c, feature_name, category) for c in notes_checks + warnings_checks]


def get_results_command(client: Client, args: Dict):
    task_id = args.get('task_id', '')
    raw: Dict = client.get_results_request(task_id)

    status = raw.get('status', '')

    if status == 'invalid':
        raise Exception("Job ID not valid or doesn't exist")

    # TODO check status for in-progress
    if status == 'TODO':
        pass

    elif status == 'complete':
        results = raw.get('results', {})
        bpa = results.get('bpa', {})
        if not bpa:
            raise Exception("Invalid response from BPA")

        job_checks = []

        for category_name, features in bpa.items():
            for feature_name, feature_contents in features.items():
                if not feature_contents:
                    # Empty list, no checks
                    continue
                checks = get_checks_from_feature(feature_contents[0], feature_name, category_name)
                job_checks.extend(checks)

    context = {'PAN-OS-BPA.JobResults(val.JobID && val.JobID === obj.JobID)': {
        'JobID': task_id,
        'Checks': job_checks,
        'Status': status
    }}

    return 'Checks received.', context, results


def test_module(client, panorama):
    client.get_documentation_request()
    panorama.get_system_time()

    demisto.results('ok')
    return '', None, None


def main():
    """
    PARSE AND VALIDATE INTEGRATION PARAMS
    """
    panorama_server = demisto.params().get('server')
    panorama_port = demisto.params().get('port')
    panorama_api_key = demisto.params().get('key')
    bpa_token = demisto.params().get('token')
    verify = not demisto.params().get('insecure', False)
    proxy = demisto.params().get('proxy')

    try:
        client = Client(bpa_token, proxy, verify)
        panorama = LightPanoramaClient(panorama_server, panorama_port, panorama_api_key, verify, proxy)
        command = demisto.command()
        LOG(f'Command being called is {command}.')
        if command == 'pan-os-bpa-submit-job':
            return_outputs(*submit_task_command(client, panorama))
        elif command == 'pan-os-bpa-get-job-results':
            return_outputs(*get_results_command(client, demisto.args()))
        elif command == 'get-remediation-documentation':
            return_outputs(*get_documentation_command(client))
        elif command == 'test-module':
            return_outputs(*test_module(client, panorama))
        else:
            raise NotImplementedError(f'Command "{command}" is not implemented.')

    except Exception as err:
        return_error(str(err))


if __name__ in ['__main__', 'builtin', 'builtins']:
    main()
