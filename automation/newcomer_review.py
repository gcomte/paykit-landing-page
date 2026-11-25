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
import zlib

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
    return validate_editorial_reports(args, bundle, args.report)


def validate_editorial_reports(args, bundle, editorial_path):
    index_hash, routes, _ = bundle
    require(args.newcomer_report.is_file() and not args.newcomer_report.is_symlink(),
            'Missing regular newcomer report')
    newcomer_bytes = args.newcomer_report.read_bytes()
    newcomer = validate_newcomer_report(newcomer_bytes, bundle, args.author_task_id, args.factual_reviewer_task_id)
    require(newcomer['reviewerTaskId'] == args.newcomer_task_id, 'Wrong newcomer task ID in accepted report')
    ids = (args.author_task_id, args.factual_reviewer_task_id, args.newcomer_task_id, args.editorial_task_id)
    require(all(text(value) for value in ids) and len(set(ids)) == 4,
            'Distinct actual author/factual/newcomer/editorial task IDs required')
    raw, report = validate_specialist_report(editorial_path, bundle, 'editorial', args.editorial_task_id)
    return {'action': 'pass', 'reviewerTaskId': args.editorial_task_id, 'inputManifestSha256': index_hash,
            'authorTaskId': args.author_task_id, 'factualReviewerTaskId': args.factual_reviewer_task_id,
            'newcomerTaskId': newcomer['reviewerTaskId'], 'newcomerReportSha256': digest(newcomer_bytes),
            'editorialReportSha256': digest(raw),
            'checkedRoutes': routes, 'nonblockingFindings': report['findings'],
            'limits': 'Binding/shape gate only; editorial judgment comes from the independent reader.'}


def validate_specialist_report(path, bundle, perspective, reviewer):
    index_hash, routes, urls = bundle
    require(path.is_file() and not path.is_symlink(), f'Missing regular {perspective} report')
    raw = path.read_bytes()
    report = load_json(raw)
    require(isinstance(report, dict) and report.get('perspective') == perspective
            and report.get('execution') == 'reading-only', f'Expected {perspective} reading-only review')
    require(report.get('reviewerTaskId') == reviewer, f'Wrong {perspective} task ID')
    require(report.get('inputManifestSha256') == index_hash, 'Stale input manifest hash')
    exact_scope(report.get('checkedRoutes'), routes, 'checked routes')
    require(report.get('passed') is True and meaningful(report.get('summary')),
            f'{perspective.capitalize()} review did not pass or lacks a meaningful summary')
    findings = report.get('findings')
    require(isinstance(findings, list), f'Missing {perspective} findings list')
    for finding in findings:
        require(isinstance(finding, dict) and all(meaningful(finding.get(key)) for key in
                ('url', 'location', 'evidence', 'impact', 'recommendation')), f'Incomplete {perspective} finding')
        require(finding['url'].split('#', 1)[0] in urls, 'Finding URL outside checked reader pages')
        require(finding.get('blocking') is False, f'Blocking or malformed {perspective} finding')
    return raw, report


def validate_security_privacy(args):
    bundle = verified_bundle(args, require_document_hash=True)
    return validate_security_privacy_reports(args, bundle, args.report)


def validate_security_privacy_reports(args, bundle, security_path):
    editorial = validate_editorial_reports(args, bundle, args.editorial_report)
    prior_ids = (editorial['authorTaskId'], editorial['factualReviewerTaskId'],
                 editorial['newcomerTaskId'], editorial['reviewerTaskId'])
    reviewer = args.security_privacy_task_id
    require(text(reviewer) and reviewer not in prior_ids,
            'Distinct actual security/privacy reviewer task ID required')
    raw, report = validate_specialist_report(security_path, bundle, 'security-privacy', reviewer)
    exact_scope(report.get('checkedTopics'), ('secrets', 'identifiers', 'diagnostics', 'data-boundaries'),
                'security/privacy topics')
    require(meaningful(report.get('limits')), 'Missing security/privacy reading limits')
    require(all('publicSource' not in finding or meaningful(finding['publicSource']) for finding in report['findings']),
            'Malformed public source citation')
    return {'action': 'pass', 'reviewerTaskId': reviewer, 'inputManifestSha256': bundle[0],
            'authorTaskId': prior_ids[0], 'factualReviewerTaskId': prior_ids[1], 'newcomerTaskId': prior_ids[2],
            'editorialTaskId': prior_ids[3], 'newcomerReportSha256': editorial['newcomerReportSha256'],
            'editorialReportSha256': editorial['editorialReportSha256'],
            'securityPrivacyReportSha256': digest(raw), 'checkedRoutes': bundle[1],
            'checkedTopics': report['checkedTopics'], 'nonblockingFindings': report['findings'],
            'limits': 'Binding/shape only; not verification of product behavior or of the cited sources.'}


def validate_failure_recovery(args):
    bundle = verified_bundle(args, require_document_hash=True)
    return validate_failure_recovery_reports(args, bundle, args.report)


def validate_failure_recovery_reports(args, bundle, recovery_path):
    security = validate_security_privacy_reports(args, bundle, args.security_privacy_report)
    prior_roles = {key: security[key] for key in ('authorTaskId', 'factualReviewerTaskId', 'newcomerTaskId', 'editorialTaskId')}
    prior_roles['securityPrivacyTaskId'] = security['reviewerTaskId']
    reviewer = args.failure_recovery_task_id
    require(text(reviewer) and reviewer not in prior_roles.values(),
            'Distinct actual failure/recovery reviewer task ID required')
    raw, report = validate_specialist_report(recovery_path, bundle, 'failure-recovery', reviewer)
    exact_scope(report.get('checkedTopics'), ('pending-and-failed', 'safe-retry', 'recovery-boundaries', 'escalation'),
                'failure/recovery topics')
    require(meaningful(report.get('limits')), 'Missing failure/recovery reading limits')
    require(all('publicSource' not in finding or meaningful(finding['publicSource']) for finding in report['findings']),
            'Malformed public source citation')
    prior_digests = {key: security[key] for key in ('newcomerReportSha256', 'editorialReportSha256', 'securityPrivacyReportSha256')}
    return {'action': 'pass', 'reviewerTaskId': reviewer, 'inputManifestSha256': bundle[0],
            **prior_roles, **prior_digests, 'failureRecoveryReportSha256': digest(raw),
            'checkedRoutes': bundle[1], 'checkedTopics': report['checkedTopics'],
            'nonblockingFindings': report['findings'],
            'limits': 'Binding/shape only; no recovery execution or verification of product behavior or cited sources.'}


def validate_usability_accessibility(args):
    bundle = verified_bundle(args, require_document_hash=True)
    return validate_usability_accessibility_reports(args, bundle, args.report)


def validate_usability_accessibility_reports(args, bundle, usability_path):
    recovery = validate_failure_recovery_reports(args, bundle, args.failure_recovery_report)
    role_keys = ('authorTaskId', 'factualReviewerTaskId', 'newcomerTaskId', 'editorialTaskId', 'securityPrivacyTaskId')
    prior_roles = {key: recovery[key] for key in role_keys}
    prior_roles['failureRecoveryTaskId'] = recovery['reviewerTaskId']
    reviewer = args.usability_accessibility_task_id
    require(text(reviewer) and reviewer not in prior_roles.values(),
            'Distinct actual usability/accessibility reviewer task ID required')
    raw, report = validate_specialist_report(usability_path, bundle, 'usability-accessibility', reviewer)
    exact_scope(report.get('checkedTopics'),
                ('guide-discovery', 'task-navigation', 'accessible-structure', 'alternatives-and-reflow'),
                'usability/accessibility topics')
    require(meaningful(report.get('limits')), 'Missing usability/accessibility reading limits')
    digest_keys = ('newcomerReportSha256', 'editorialReportSha256', 'securityPrivacyReportSha256',
                   'failureRecoveryReportSha256')
    prior_digests = {key: recovery[key] for key in digest_keys}
    return {'action': 'pass', 'reviewerTaskId': reviewer, 'inputManifestSha256': bundle[0],
            **prior_roles, **prior_digests, 'usabilityAccessibilityReportSha256': digest(raw),
            'checkedRoutes': bundle[1], 'checkedTopics': report['checkedTopics'],
            'nonblockingFindings': report['findings'],
            'limits': 'Reading report binding/shape only; no browser, assistive technology or conformance verification.'}


VISUAL_VIEWPORTS = {'desktop': (1440, 900), 'phone': (375, 812)}


def validate_visual_png(data, expected_width, viewport_height):
    require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'Screenshot is not a PNG')
    offset = 8
    payloads = []
    pixel_bytes = None
    row_size = 0
    closed = False
    after_data = False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], 'big')
        end = offset + 12 + size
        require(end <= len(data), 'Truncated PNG chunk')
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:end - 4]
        crc = int.from_bytes(data[end - 4:end], 'big')
        require(zlib.crc32(payload, zlib.crc32(kind)) == crc, 'Corrupt PNG chunk')
        if offset == 8:
            require(kind == b'IHDR' and size == 13, 'Missing PNG metadata')
            width = int.from_bytes(payload[:4], 'big')
            height = int.from_bytes(payload[4:8], 'big')
            require(width == expected_width and viewport_height <= height <= 32768,
                    'Screenshot PNG dimensions do not match the full-page viewport')
            # Chromium emits noninterlaced 8-bit RGB/RGBA. Refuse other encodings.
            require(payload[8] == 8 and payload[9] in (2, 6) and payload[10:] == b'\0\0\0', 'Unsupported screenshot PNG format')
            row_size = 1 + width * (3 if payload[9] == 2 else 4)
            pixel_bytes = row_size * height
        elif kind == b'IDAT':
            require(not after_data, 'Non-consecutive PNG image data')
            payloads.append(payload)
        elif kind == b'IEND':
            require(size == 0 and end == len(data), 'Invalid PNG ending')
            closed = True
            break
        else:
            require(kind.isalpha() and kind[:1].islower(), 'Unexpected PNG critical chunk')
            after_data = bool(payloads)
        offset = end
    if not closed or not payloads or pixel_bytes is None or row_size <= 0:
        raise ValueError('Incomplete PNG image data')
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(b''.join(payloads), pixel_bytes + 1)
    except zlib.error as error:
        raise ValueError('Invalid PNG pixel data') from error
    require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and len(pixels) == pixel_bytes
            and all(filter_type <= 4 for filter_type in pixels[::row_size]), 'Invalid PNG pixel data')


def verified_visual_bundle(args, bundle):
    index_hash, routes, urls = bundle
    require(urls[0].startswith('http://127.0.0.1:'), 'Visual review needs a current-build loopback capture')
    reader_index = args.inputs.read_bytes()
    require(digest(reader_index) == index_hash, 'Reader index changed during validation')
    documents = load_json(reader_index)['documents']
    path = args.visual_inputs
    require(path.is_file() and not path.is_symlink(), 'Missing regular visual index')
    raw = path.read_bytes()
    manifest = load_json(raw)
    require(isinstance(manifest, dict) and manifest.get('kind') == 'rendered-public-visual-input'
            and manifest.get('inputManifestSha256') == index_hash, 'Stale or invalid visual input manifest')
    expected = {f'{i:02d}-{view}.png': (routes[i], doc['html']['sha256'], view)
                for i, doc in enumerate(documents) for view in VISUAL_VIEWPORTS}
    shots = manifest.get('screenshots')
    if not isinstance(shots, list) or not all(isinstance(shot, dict) for shot in shots):
        raise ValueError('Missing screenshot list')
    exact_scope([shot.get('file') for shot in shots], expected, 'visual screenshots')
    root = path.parent.resolve()
    for shot in shots:
        filename = shot['file']
        route, html_hash, view = expected[filename]
        width, height = VISUAL_VIEWPORTS[view]
        require((shot.get('route'), shot.get('htmlSha256'), shot.get('view')) == (route, html_hash, view),
                'Screenshot binding does not match rendered HTML/route/view')
        viewport = shot.get('viewport')
        require(isinstance(viewport, dict) and viewport == {'width': width, 'height': height}
                and all(type(value) is int for value in viewport.values()), 'Wrong screenshot viewport')
        file = root / filename
        require(file.is_file() and not file.is_symlink() and file.resolve() == file, 'Missing regular screenshot')
        data = file.read_bytes()
        require(type(shot.get('bytes')) is int and shot['bytes'] == len(data)
                and shot.get('sha256') == digest(data), 'Screenshot length/hash mismatch')
        validate_visual_png(data, width, height)
    return digest(raw), list(expected)


def validate_visual_design(args):
    bundle = verified_bundle(args, require_document_hash=True)
    usability = validate_usability_accessibility_reports(args, bundle, args.usability_accessibility_report)
    role_keys = ('authorTaskId', 'factualReviewerTaskId', 'newcomerTaskId', 'editorialTaskId',
                 'securityPrivacyTaskId', 'failureRecoveryTaskId')
    prior_roles = {key: usability[key] for key in role_keys}
    prior_roles['usabilityAccessibilityTaskId'] = usability['reviewerTaskId']
    reviewer = args.visual_design_task_id
    require(text(reviewer) and reviewer not in prior_roles.values(), 'Distinct actual visual design reviewer task ID required')
    visual_hash, screenshots = verified_visual_bundle(args, bundle)
    raw, report = validate_specialist_report(args.report, bundle, 'visual-design', reviewer)
    require(report.get('visualManifestSha256') == visual_hash, 'Stale visual manifest hash in review report')
    exact_scope(report.get('checkedScreenshots'), screenshots, 'checked screenshots')
    exact_scope(report.get('checkedTopics'), ('hierarchy', 'legibility', 'layout', 'consistency'), 'visual design topics')
    require(meaningful(report.get('limits')), 'Missing visual design reading limits')
    require(all(finding.get('viewport') in VISUAL_VIEWPORTS for finding in report['findings']),
            'Missing or unknown visual finding viewport')
    prior_digests = {key: value for key, value in usability.items() if key.endswith('ReportSha256')}
    return {'action': 'pass', 'reviewerTaskId': reviewer, 'inputManifestSha256': bundle[0],
            'visualManifestSha256': visual_hash, 'visualDesignReportSha256': digest(raw), **prior_roles, **prior_digests,
            'checkedRoutes': bundle[1], 'checkedScreenshots': screenshots, 'checkedTopics': report['checkedTopics'],
            'nonblockingFindings': report['findings'],
            'limits': 'Screenshot and reading report binding only; does not establish visual judgment or conformance.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    cap = commands.add_parser('capture')
    cap.add_argument('--document', type=Path, required=True)
    cap.add_argument('--version', required=True)
    cap.add_argument('--base-url', required=True)
    cap.add_argument('--output', type=Path, required=True)
    for command in ('validate', 'validate-editorial', 'validate-security-privacy', 'validate-failure-recovery',
                    'validate-usability-accessibility', 'validate-visual-design'):
        val = commands.add_parser(command)
        val.add_argument('--inputs', type=Path, required=True)
        val.add_argument('--report', type=Path, required=True)
        val.add_argument('--document', type=Path, required=True)
        val.add_argument('--author-task-id', required=True)
        val.add_argument('--factual-reviewer-task-id', required=True)
        if command != 'validate':
            val.add_argument('--newcomer-report', type=Path, required=True)
            val.add_argument('--newcomer-task-id', required=True)
            val.add_argument('--editorial-task-id', required=True)
        if command not in ('validate', 'validate-editorial'):
            val.add_argument('--editorial-report', type=Path, required=True)
            val.add_argument('--security-privacy-task-id', required=True)
        if command in ('validate-failure-recovery', 'validate-usability-accessibility', 'validate-visual-design'):
            val.add_argument('--security-privacy-report', type=Path, required=True)
            val.add_argument('--failure-recovery-task-id', required=True)
        if command in ('validate-usability-accessibility', 'validate-visual-design'):
            val.add_argument('--failure-recovery-report', type=Path, required=True)
            val.add_argument('--usability-accessibility-task-id', required=True)
        if command == 'validate-visual-design':
            val.add_argument('--usability-accessibility-report', type=Path, required=True)
            val.add_argument('--visual-design-task-id', required=True)
            val.add_argument('--visual-inputs', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = {'capture': capture, 'validate': validate, 'validate-editorial': validate_editorial,
                  'validate-security-privacy': validate_security_privacy,
                  'validate-failure-recovery': validate_failure_recovery,
                  'validate-usability-accessibility': validate_usability_accessibility,
                  'validate-visual-design': validate_visual_design}[args.command](args)
    except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException) as error:
        print(json.dumps({'action': 'refused', 'detail': str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
