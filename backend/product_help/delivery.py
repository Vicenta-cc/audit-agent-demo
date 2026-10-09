"""Keep report navigation usable even when a model omits Markdown links."""
import json
import re


def complete_report_links(answer, transcript, history_count):
    reports = {}
    for message in transcript[history_count:]:
        if message.get('role') != 'tool' or message.get('name') != 'list_completed_reports':
            continue
        try:
            result = json.loads(message.get('content') or '{}')
            if result.get('status') != 'ok':
                continue
            for item in result['data']['reports']:
                url, title = item['url'], item['title']
                if title and re.fullmatch(r'/investigation/[A-Za-z0-9%._~-]+/report\?report=report-version%3[Aa][A-Za-z0-9_-]+', url):
                    reports[url] = title
        except (ValueError, KeyError, TypeError):
            continue
    original = answer
    for url, title in reports.items():
        if '](' + url + ')' in answer:
            continue
        label = re.sub(r'([\\`*_[\]<>|])', r'\\\1', title).replace('\n', ' ')
        link = f'[{label}]({url})'
        parts = re.split(r'(!?\[[^\]]*\]\([^\n)]*\)|`[^`]*`)', answer)
        for index in range(0, len(parts), 2):
            if title in parts[index]:
                parts[index] = parts[index].replace(title, link, 1)
                answer = ''.join(parts)
                break
        else:
            answer += '\n\n' + link
    if answer != original and transcript and transcript[-1].get('role') == 'assistant' and not transcript[-1].get('tool_calls'):
        transcript = [*transcript[:-1], {**transcript[-1], 'content': answer}]
    return answer, transcript
