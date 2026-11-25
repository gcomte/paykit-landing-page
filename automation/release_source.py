"""Choose and freeze documentation sources from the public release catalog."""
from datetime import datetime
import re
import subprocess
import json
import urllib.error
import urllib.request
import urllib.parse

API = 'https://api.github.com/repos/pubky/paykit-rs'
RELEASE_PATHS = ('/repos/pubky/paykit-rs/releases', '/repositories/1100020143/releases')
REPOSITORY = 'pubky/paykit-rs'
POLICY = {'repository': REPOSITORY, 'selection': 'newest-release'}
STABLE_POLICY = {'repository': REPOSITORY, 'selection': 'highest-stable'}
RELEASE_FIELDS = ('id', 'tag_name', 'published_at', 'prerelease', 'html_url')
SHA = re.compile(r'[0-9a-f]{40}')
SEMVER_CORE = r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'


def configured_sources(config):
    """Old fixtures without releasedSource only use the original selected-source track."""
    if 'releasedSource' not in config:
        return {'coming-soon': config['source']}
    if config['releasedSource'] != STABLE_POLICY:
        raise ValueError('Unsupported Released source policy')
    return {'released': config['releasedSource'], 'coming-soon': config['source']}


def qualified_ref(repository, kind, ref):
    if repository != REPOSITORY or kind not in ('branch', 'tag'):
        raise ValueError('Unsupported public source repository or ref kind')
    if not isinstance(ref, str) or len(ref.encode()) > 255 or '\x00' in ref:
        raise ValueError('Invalid source ref')
    qualified = 'refs/' + ('tags/' if kind == 'tag' else 'heads/') + ref
    if not ref or subprocess.run(['git', 'check-ref-format', qualified], capture_output=True).returncode:
        raise ValueError('Invalid source ref')
    return qualified


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
    qualified_ref(REPOSITORY, 'tag', ref)
    url = urllib.parse.urlsplit(record['html_url'])
    if (url.scheme != 'https' or url.netloc != 'github.com' or url.query or url.fragment
            or urllib.parse.unquote(url.path) != '/pubky/paykit-rs/releases/tag/' + ref):
        raise ValueError('Invalid public release URL')
    return {key: record[key] for key in RELEASE_FIELDS}


def stable_version(release):
    """Stable SemVer precedence; leading v is the Git tag convention, not normalized output."""
    if release['prerelease']:
        return None
    match = re.fullmatch(r'v?' + SEMVER_CORE + r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?',
                         release['tag_name'])
    return tuple(map(int, match.groups())) if match else None


def validate_frozen(source, frozen, commit, *, require_object=False):
    selection = frozen['sourceSelection']
    if source in (POLICY, STABLE_POLICY):
        release = release_identity(frozen['release'])
        if source == STABLE_POLICY and stable_version(release) is None:
            raise ValueError('Frozen release is not a published stable SemVer')
        expected = {'repository': source['repository'], 'kind': 'tag', 'ref': release['tag_name']}
        if frozen.get('sourcePolicy') != source or selection != expected:
            raise ValueError('Frozen source selection does not match the release policy')
    elif 'selection' in source or selection != source:
        raise ValueError('Frozen source selection does not match the configured source')
    if frozen['sourceCommit'] != commit:
        raise ValueError('Frozen source selection does not match the expected commit')
    if require_object or 'sourceObject' in frozen or 'qualifiedRef' in frozen:
        qualified = qualified_ref(selection['repository'], selection['kind'], selection['ref'])
        if (frozen.get('qualifiedRef') != qualified or not SHA.fullmatch(frozen.get('sourceObject') or '')
                or not SHA.fullmatch(commit)):
            raise ValueError('Frozen source selection requires the exact qualified ref, object and commit')


def source_matches(version, frozen):
    selection = frozen['sourceSelection']
    return (version['commit'] == frozen['sourceCommit'] and
            ('sourcePolicy' not in frozen or (version.get('kind', 'tag') == 'tag'
             and version.get('ref', version.get('tag')) == selection['ref']))
            and ('sourceObject' not in frozen or version.get('object', frozen['sourceObject']) == frozen['sourceObject']))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Public-source HTTP redirects are not allowed')


def read_public(url, *, limit=2_000_000, missing=False):
    """Bound anonymous reads. Callers construct URLs at the approved, fixed origins."""
    request = urllib.request.Request(url, headers={
        'User-Agent': 'paykit-docs-public-source', 'Cache-Control': 'no-cache'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            status, data, headers = response.status, response.read(limit + 1), response.headers
    except urllib.error.HTTPError as error:
        with error:
            if not missing or error.code != 404:
                raise
            status, data, headers = error.code, error.read(limit + 1), error.headers
    if status != 200 and not (missing and status == 404):
        raise ValueError('Unexpected public HTTP status: ' + str(status))
    if len(data) > limit:
        raise ValueError('Public response too large: ' + url)
    return status, data, headers


def fetch_release_page(url):
    _, data, headers = read_public(url)
    return json.loads(data), headers.get('Link', '')


def release_catalog(fetcher):
    rows = []
    for number in range(1, 26):
        page, link = fetcher(f'{API}/releases?per_page=100&page={number}')
        if not isinstance(page, list):
            raise ValueError('Invalid public release list')
        for row in page:
            if (not isinstance(row, dict) or type(row.get('draft')) is not bool
                    or 'published_at' not in row):
                raise ValueError('Invalid public release record')
            if not row['draft'] and row['published_at'] is not None:
                rows.append(release_identity(row))
        next_page = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not next_page:
            break
        location = urllib.parse.urlsplit(next_page[1])
        if (location.scheme != 'https' or location.netloc != 'api.github.com'
                or location.path not in RELEASE_PATHS or location.fragment
                or urllib.parse.parse_qs(location.query, keep_blank_values=True) != {
                    'per_page': ['100'], 'page': [str(number + 1)]}):
            raise ValueError('Invalid public release pagination')
    else:
        raise ValueError('Public release pagination exceeds the scan limit')
    if not rows:
        raise ValueError('No published public releases are available')
    return rows


def select_sources(policies, resolver, fetcher=None, *, require_object=False):
    dynamic = [s for s in policies.values() if 'selection' in s]
    if any(s not in (POLICY, STABLE_POLICY) for s in dynamic):
        raise ValueError('Unsupported public source selection policy')
    rows = release_catalog(fetcher or fetch_release_page) if dynamic else []
    resolved, frozen = {}, {}
    for version, policy in policies.items():
        result = {}
        if 'selection' not in policy:
            selection = dict(policy)
        else:
            published = lambda row: (datetime.fromisoformat(row['published_at']), row['id'])
            if policy == STABLE_POLICY:
                stable = [(stable_version(row), row) for row in rows]
                stable = [(key, row) for key, row in stable if key is not None]
                if not stable:
                    raise ValueError('No published stable SemVer release is available')
                _, record = max(stable, key=lambda pair: (pair[0], published(pair[1])))
            else:
                record = max(rows, key=published)
            selection = {'repository': REPOSITORY, 'kind': 'tag', 'ref': record['tag_name']}
            result.update(sourcePolicy=dict(policy), release=dict(record))
        identity = tuple(selection[key] for key in ('repository', 'kind', 'ref'))
        if identity not in resolved:
            resolved[identity] = resolver(*identity)
        resolution = resolved[identity]
        if isinstance(resolution, str):  # Commit-only legacy callers, including explicit test selectors.
            resolution = {'sourceCommit': resolution}
        result = {**resolution, **result, 'sourceSelection': selection}
        if policy == STABLE_POLICY or require_object or 'sourceObject' in result or 'qualifiedRef' in result:
            validate_frozen(policy, result, result['sourceCommit'], require_object=True)
        frozen[version] = result
    return frozen


def select_source(source, resolver, fetcher=None, *, require_object=False):
    return select_sources({'source': source}, resolver, fetcher, require_object=require_object)['source']


def read_ref(repository, kind, ref, record, *, expected_object=None):
    """Resolve the exact named ref and peel annotated tags, retaining the outer object."""
    qualified = qualified_ref(repository, kind, ref)
    selected = record(API + '/git/ref/' + urllib.parse.quote(qualified[5:], safe='/'), 'ref.json')
    target = selected['object']
    object_sha = target['sha']
    if selected['ref'] != qualified or not SHA.fullmatch(object_sha):
        raise ValueError('Invalid exact ref receipt')
    if expected_object is not None and object_sha != expected_object:
        raise ValueError('Source ref object moved since the frozen selection')
    seen = set()
    while kind == 'tag' and target['type'] == 'tag':
        sha = target['sha']
        if sha in seen or len(seen) >= 16 or not SHA.fullmatch(sha):
            raise ValueError('Invalid or cyclic tag object')
        tag = record(API + '/git/tags/' + sha, 'tag.json' if not seen else f'tag-{sha}.json')
        seen.add(sha)
        if tag['sha'] != sha:
            raise ValueError('Mismatched tag object')
        target = tag['object']
    if target['type'] != 'commit' or not SHA.fullmatch(target['sha']):
        raise ValueError('Source ref did not resolve to a commit')
    return {'qualifiedRef': qualified, 'sourceObject': object_sha, 'sourceCommit': target['sha']}
