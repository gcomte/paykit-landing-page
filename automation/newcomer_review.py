#!/usr/bin/env python3
"""Capture public rendered reader inputs; validate independent reading reports."""
import argparse
import datetime
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

APPROVED_SITES = ('https://paykit-landing-page-lake.vercel.app',
                  'https://paykit-landing-page-git-docs-automation-g-a0aa.vercel.app')
TIMEOUT = 15


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def text(value):
    return isinstance(value, str) and bool(value.strip())


def json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON field: ' + key)
        result[key] = value
    return result


def load_json(raw):
    return json.loads(raw, object_pairs_hook=json_object)


def document_scope(path, version):
    raw = path.read_bytes()
    doc = load_json(raw)
    require(version in ('coming-soon', 'released'), 'Unknown documentation version')
    require(isinstance(doc, dict) and isinstance(doc.get('version'), dict)
            and bool(doc['version']), 'Missing document version metadata')
    require(re.fullmatch(r'[0-9a-f]{40}', str(doc['version'].get('commit', ''))),
            'Missing document source commit')
    day = doc.get('lastUpdated')
    require(isinstance(day, str) and datetime.date.fromisoformat(day).isoformat() == day,
            'Missing document generation date')
    pages = doc.get('pages')
    require(isinstance(pages, list) and bool(pages), 'Missing document pages')
    slugs = []
    for page in pages:
        require(isinstance(page, dict) and isinstance(page.get('slug'), str)
                and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', page['slug']), 'Malformed page slug')
        slugs.append(page['slug'])
    require(len(slugs) == len(set(slugs)), 'Duplicate document pages')
    return raw, doc, ['/docs/' + version + '/' + slug for slug in slugs]


def origin(base):
    require(isinstance(base, str), 'Unsupported origin')
    parsed = urllib.parse.urlsplit(base)
    require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            and parsed.path in ('', '/'), 'Expected a bare approved origin')
    if base in APPROVED_SITES:
        return base
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = False
    require(parsed.scheme == 'http' and loopback and parsed.port is not None,
            'Only explicit loopback HTTP or approved HTTPS aliases are allowed')
    return base.rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError('Reader capture refuses redirects')


def capture(args):
    raw, doc, routes = document_scope(args.document, args.version)
    base = origin(args.base_url)
    require(args.output.is_absolute(), 'Output must be an absolute new directory')
    args.output.mkdir(exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    rows = []
    for i, route in enumerate(['/docs', *routes]):
        row = {'route': route, 'url': base + route}
        for kind, suffix in (('html', ''), ('md', '/index.md')):
            request = urllib.request.Request(base + route + suffix,
                                            headers={'Cache-Control': 'no-cache',
                                                     'User-Agent': 'paykit-newcomer-reader'})
            with opener.open(request, timeout=TIMEOUT) as response:
                require(response.status == 200, 'Expected HTTP 200')
                expected = 'text/html' if kind == 'html' else 'text/markdown'
                require(response.headers.get_content_type() == expected, 'Unexpected response type')
                body = response.read()
                require(bool(body), 'Empty reader body')
                metadata = {'status': response.status, 'headers': list(response.headers.raw_items())}
            path = args.output / f'{i:02d}.{kind}'
            path.write_bytes(body)
            row[kind] = {'path': str(path), 'bytes': len(body), 'sha256': digest(body), **metadata}
        rows.append(row)
    manifest = {'kind': 'rendered-public-reader-input', 'version': args.version,
                'versionMetadata': doc['version'], 'lastUpdated': doc['lastUpdated'],
                'documentSha256': digest(raw), 'entryUrl': base + '/docs',
                'pageRoutes': routes, 'documents': rows}
    index = args.output / 'index.json'
    index.write_text(json.dumps(manifest, indent=2) + '\n')
    return {'action': 'captured', 'inputs': str(index), 'inputManifestSha256': digest(index.read_bytes()),
            'documents': len(rows)}


def meaningful(value):
    return text(value) and value.strip().lower() not in ('unknown', 'n/a', 'none', 'todo', 'pass', 'ok')


def exact_scope(actual, expected, label):
    require(isinstance(actual, list) and all(isinstance(item, str) for item in actual)
            and len(actual) == len(set(actual)) and set(actual) == set(expected),
            'Incomplete or duplicate ' + label)


def verified_bundle(args, *, require_document_hash=False):
    require(args.inputs.is_file() and not args.inputs.is_symlink(), 'Missing regular input index')
    index_bytes = args.inputs.read_bytes()
    manifest = load_json(index_bytes)
    require(isinstance(manifest, dict) and manifest.get('kind') == 'rendered-public-reader-input',
            'Unknown reader input manifest')
    raw, doc, routes = document_scope(args.document, manifest.get('version'))
    require(manifest.get('versionMetadata') == doc['version']
            and manifest.get('lastUpdated') == doc['lastUpdated'], 'Stale source/version/date metadata')
    if require_document_hash or 'documentSha256' in manifest:
        require(manifest.get('documentSha256') == digest(raw), 'Missing or stale document hash')
    require(manifest.get('pageRoutes') == routes, 'Stale or incomplete page scope')
    entry = manifest.get('entryUrl')
    require(isinstance(entry, str) and entry.endswith('/docs'), 'Invalid entry URL')
    base = origin(entry[:-len('/docs')])
    expected_routes = ['/docs', *routes]
    rows = manifest.get('documents')
    require(isinstance(rows, list) and len(rows) == len(expected_routes), 'Incomplete reader bodies')
    root = args.inputs.parent.resolve()
    urls = []
    for i, (row, route) in enumerate(zip(rows, expected_routes)):
        require(isinstance(row, dict) and row.get('route') == route
                and row.get('url') == base + route, 'Stale or duplicate reader route/URL')
        urls.append(row['url'])
        for kind in ('html', 'md'):
            binding = row.get(kind)
            require(isinstance(binding, dict) and text(binding.get('path')), 'Missing reader body binding')
            path = Path(binding['path'])
            path = path if path.is_absolute() else root / path
            expected = root / f'{i:02d}.{kind}'
            require(path == expected and not path.is_symlink() and path.is_file()
                    and path.resolve() == expected, 'Reader body must be its own regular bundle file')
            body = path.read_bytes()
            require(type(binding.get('bytes')) is int and binding['bytes'] == len(body) and bool(body)
                    and binding.get('sha256') == digest(body), 'Reader body length/hash mismatch')
    return digest(index_bytes), expected_routes, urls


def validate(args):
    bundle = verified_bundle(args)
    return validate_newcomer_report(args.report.read_bytes(), bundle,
                                   args.author_task_id, args.factual_reviewer_task_id)


def validate_newcomer_report(raw, bundle, author, factual):
    index_hash, expected_routes, urls = bundle
    report = load_json(raw)
    require(isinstance(report, dict), 'Missing newcomer report object')
    require(report.get('perspective') == 'newcomer' and report.get('execution') == 'reading-only',
            'Expected newcomer reading-only review, not SDK execution')
    reviewer = report.get('reviewerTaskId')
    require(all(text(value) for value in (author, factual, reviewer))
            and len({author, factual, reviewer}) == 3, 'Distinct actual author/factual/newcomer task IDs required')
    require(report.get('inputManifestSha256') == index_hash, 'Stale input manifest hash')
    exact_scope(report.get('checkedRoutes'), expected_routes, 'checked routes')
    require(report.get('passed') is True, 'Newcomer report did not pass')
    summary = report.get('summary')
    require(meaningful(summary) or (isinstance(summary, dict) and meaningful(summary.get('outcome'))),
            'Missing newcomer summary/outcome')
    journeys = report.get('journeys')
    require(isinstance(journeys, list) and bool(journeys), 'Missing discovery journeys')
    for journey in journeys:
        require(isinstance(journey, dict) and meaningful(journey.get('goal')), 'Missing journey goal')
        path, observations = journey.get('path'), journey.get('observations')
        require(isinstance(path, list) and bool(path)
                and all(isinstance(url, str) and url.split('#', 1)[0] in urls for url in path),
                'Journey paths must name actual reader URLs')
        require(isinstance(observations, list) and bool(observations)
                and all(meaningful(item) for item in observations), 'Missing journey observations')
    require(isinstance(report.get('unansweredQuestions'), list)
            and all(meaningful(item) or (isinstance(item, dict) and meaningful(item.get('question')))
                    for item in report['unansweredQuestions']), 'Malformed unanswered questions')
    limits = report.get('limits')
    require(meaningful(limits) or (isinstance(limits, list) and bool(limits)
                                  and all(meaningful(item) for item in limits)), 'Missing reading limits')
    findings = report.get('findings')
    require(isinstance(findings, list), 'Missing findings list')
    ids = set()
    for finding in findings:
        require(isinstance(finding, dict), 'Malformed newcomer finding')
        require(all(meaningful(finding.get(key)) for key in
                    ('id', 'url', 'location', 'evidence', 'readerQuestion', 'impact', 'recommendation')),
                'Incomplete newcomer finding')
        require(finding['id'] not in ids, 'Duplicate finding ID')
        ids.add(finding['id'])
        require(finding.get('severity') in ('blocker', 'critical', 'high', 'major', 'medium',
                                            'minor', 'low', 'suggestion', 'info'), 'Unknown finding severity')
        require(type(finding.get('blocking')) is bool, 'Finding blocking must be a boolean')
        require(finding['url'].split('#', 1)[0] in urls, 'Finding URL outside checked reader pages')
        require(finding['severity'] != 'blocker' or finding['blocking'], 'Severity blocker must block')
        require(not finding['blocking'], 'Blocking newcomer finding: ' + finding['id'])
    return {'action': 'pass', 'reviewerTaskId': reviewer, 'inputManifestSha256': index_hash,
            'checkedRoutes': expected_routes, 'nonblockingFindings': findings,
            'limits': 'Binding/shape gate only; semantic review is the independent reader report.'}


def validate_editorial(args):
    bundle = verified_bundle(args, require_document_hash=True)
    index_hash, routes, urls = bundle
    require(args.newcomer_report.is_file() and not args.newcomer_report.is_symlink(),
            'Missing regular newcomer report')
    newcomer_bytes = args.newcomer_report.read_bytes()
    newcomer = validate_newcomer_report(newcomer_bytes, bundle, args.author_task_id, args.factual_reviewer_task_id)
    require(newcomer['reviewerTaskId'] == args.newcomer_task_id, 'Wrong newcomer task ID in accepted report')
    require(args.report.is_file() and not args.report.is_symlink(), 'Missing regular editorial report')
    report = load_json(args.report.read_bytes())
    require(isinstance(report, dict) and report.get('perspective') == 'editorial'
            and report.get('execution') == 'reading-only', 'Expected editorial reading-only review')
    ids = (args.author_task_id, args.factual_reviewer_task_id, args.newcomer_task_id, args.editorial_task_id)
    require(all(text(value) for value in ids) and len(set(ids)) == 4,
            'Distinct actual author/factual/newcomer/editorial task IDs required')
    require(report.get('reviewerTaskId') == args.editorial_task_id, 'Wrong editorial task ID')
    require(report.get('inputManifestSha256') == index_hash, 'Stale input manifest hash')
    exact_scope(report.get('checkedRoutes'), routes, 'checked routes')
    require(report.get('passed') is True and meaningful(report.get('summary')),
            'Editorial review did not pass or lacks a meaningful summary')
    findings = report.get('findings')
    require(isinstance(findings, list), 'Missing editorial findings list')
    for finding in findings:
        require(isinstance(finding, dict) and all(meaningful(finding.get(key)) for key in
                ('url', 'location', 'evidence', 'impact', 'recommendation')), 'Incomplete editorial finding')
        require(finding['url'].split('#', 1)[0] in urls, 'Finding URL outside checked reader pages')
        require(finding.get('blocking') is False, 'Blocking or malformed editorial finding')
    return {'action': 'pass', 'reviewerTaskId': args.editorial_task_id, 'inputManifestSha256': index_hash,
            'authorTaskId': args.author_task_id, 'factualReviewerTaskId': args.factual_reviewer_task_id,
            'newcomerTaskId': newcomer['reviewerTaskId'], 'newcomerReportSha256': digest(newcomer_bytes),
            'checkedRoutes': routes, 'nonblockingFindings': findings,
            'limits': 'Binding/shape gate only; editorial judgment comes from the independent reader.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    cap = commands.add_parser('capture')
    cap.add_argument('--document', type=Path, required=True)
    cap.add_argument('--version', required=True)
    cap.add_argument('--base-url', required=True)
    cap.add_argument('--output', type=Path, required=True)
    for command in ('validate', 'validate-editorial'):
        val = commands.add_parser(command)
        val.add_argument('--inputs', type=Path, required=True)
        val.add_argument('--report', type=Path, required=True)
        val.add_argument('--document', type=Path, required=True)
        val.add_argument('--author-task-id', required=True)
        val.add_argument('--factual-reviewer-task-id', required=True)
        if command == 'validate-editorial':
            val.add_argument('--newcomer-report', type=Path, required=True)
            val.add_argument('--newcomer-task-id', required=True)
            val.add_argument('--editorial-task-id', required=True)
    args = parser.parse_args(argv)
    try:
        result = {'capture': capture, 'validate': validate, 'validate-editorial': validate_editorial}[args.command](args)
    except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException) as error:
        print(json.dumps({'action': 'refused', 'detail': str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
