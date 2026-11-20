"""Choose and freeze the newest published public release, including prereleases."""
from datetime import datetime
import re
import subprocess
import json
import urllib.request
import urllib.parse

API = 'https://api.github.com/repos/pubky/paykit-rs'
POLICY = {'repository': 'pubky/paykit-rs', 'selection': 'newest-release'}
RELEASE_FIELDS = ('id', 'tag_name', 'published_at', 'prerelease', 'html_url')


def release_identity(record):
    if (not isinstance(record, dict) or not set(RELEASE_FIELDS) <= record.keys()
            or type(record['id']) is not int or record['id'] <= 0 or not isinstance(record['html_url'], str)
            or type(record['prerelease']) is not bool or record.get('draft', False) is not False):
        raise ValueError('Invalid published release identity')
    published = record['published_at']
    if not isinstance(published, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', published):
        raise ValueError('Invalid release publication timestamp')
    datetime.fromisoformat(published)
    ref = record['tag_name']
    if (not isinstance(ref, str) or '\x00' in ref
            or subprocess.run(['git', 'check-ref-format', 'refs/tags/' + ref], capture_output=True).returncode):
        raise ValueError('Invalid source ref')
    url = urllib.parse.urlsplit(record['html_url'])
    if (url.scheme != 'https' or url.netloc != 'github.com' or url.query or url.fragment
            or urllib.parse.unquote(url.path) != '/pubky/paykit-rs/releases/tag/' + ref):
        raise ValueError('Invalid public release URL')
    return {key: record[key] for key in RELEASE_FIELDS}


def validate_frozen(source, frozen, commit):
    selection = frozen['sourceSelection']
    if source == POLICY:
        release = release_identity(frozen['release'])
        expected = {'repository': source['repository'], 'kind': 'tag', 'ref': release['tag_name']}
        if frozen.get('sourcePolicy') != source or selection != expected:
            raise ValueError('Frozen source selection does not match the release policy')
    elif 'selection' in source or selection != source:
        raise ValueError('Frozen source selection does not match the configured source')
    if frozen['sourceCommit'] != commit:
        raise ValueError('Frozen source selection does not match the expected commit')


def source_matches(version, frozen):
    selection = frozen['sourceSelection']
    return (version['commit'] == frozen['sourceCommit'] and
            ('sourcePolicy' not in frozen or (version.get('kind', 'tag') == 'tag'
             and version.get('ref', version.get('tag')) == selection['ref'])))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Public-source HTTP redirects are not allowed')


def fetch_release_page(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'paykit-docs-release-selection', 'Accept': 'application/vnd.github+json'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        data = response.read(2_000_001)
        if response.status != 200 or len(data) > 2_000_000:
            raise ValueError('Invalid public release response status or size')
        return json.loads(data), response.headers.get('Link', '')


def select_source(source, resolver, fetcher=None):
    if 'selection' not in source:
        return {'sourceSelection': dict(source), 'sourceCommit': resolver(
            source['repository'], source['kind'], source['ref'])}
    if source != POLICY:
        raise ValueError('Unsupported public source selection policy')
    fetcher = fetcher or fetch_release_page
    rows, url, seen = [], API + '/releases?per_page=100&page=1', set()
    while url:
        location = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(location.query)
        if (url in seen or url.split('?', 1)[0] != API + '/releases'
                or set(query) != {'per_page', 'page'} or query['per_page'] != ['100']
                or len(query['page']) != 1 or not re.fullmatch('[1-9][0-9]*', query['page'][0])
                or location.fragment):
            raise ValueError('Invalid public release pagination')
        seen.add(url)
        page, link = fetcher(url)
        if not isinstance(page, list):
            raise ValueError('Invalid public release list')
        for row in page:
            if (not isinstance(row, dict) or type(row.get('draft')) is not bool
                    or 'published_at' not in row):
                raise ValueError('Invalid public release record')
            if not row['draft'] and row['published_at'] is not None:
                rows.append(release_identity(row))
        next_page = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = next_page[1] if next_page else None
    if not rows:
        raise ValueError('No published public releases are available')
    record = max(rows, key=lambda row: datetime.fromisoformat(row['published_at']))
    ref = record['tag_name']
    selection = {'repository': source['repository'], 'kind': 'tag', 'ref': ref}
    return {'sourcePolicy': dict(source), 'sourceSelection': selection,
            'release': {key: record[key] for key in RELEASE_FIELDS},
            'sourceCommit': resolver(selection['repository'], 'tag', ref)}
