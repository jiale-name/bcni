# -*- coding: utf-8 -*-
"""提取 docx 正文文本（含表格），输出到 _docx_dump.md"""
import os
import re
import zipfile
from xml.etree import ElementTree as ET

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
}
W = NS['w']

SRC = r'D:\gan\16个人项目\2026顶配版AI数字化人才平台详细技术与功能需求.docx'
OUT = r'D:\gan\16个人项目\_docx_dump.md'


def para_text(p):
    parts = []
    for node in p.iter():
        tag = node.tag
        if tag == f'{{{W}}}t':
            parts.append(node.text or '')
        elif tag == f'{{{W}}}tab':
            parts.append('\t')
        elif tag == f'{{{W}}}br':
            parts.append('\n')
    return ''.join(parts)


def para_style(p):
    ppr = p.find(f'{{{W}}}pPr')
    if ppr is None:
        return ''
    ps = ppr.find(f'{{{W}}}pStyle')
    if ps is not None:
        return ps.get(f'{{{W}}}val', '')
    return ''


def walk(elem, out, depth=0):
    for child in elem:
        tag = child.tag
        if tag == f'{{{W}}}p':
            t = para_text(child).strip()
            st = para_style(child)
            if t:
                if st.lower().startswith('heading') or st.lower().startswith('标题'):
                    out.append(f'\n### [{st}] {t}\n')
                else:
                    out.append(t)
        elif tag == f'{{{W}}}tbl':
            out.append('\n[TABLE START]')
            for tr in child.findall(f'{{{W}}}tr'):
                cells = []
                for tc in tr.findall(f'{{{W}}}tc'):
                    ct = []
                    for p in tc.findall(f'{{{W}}}p'):
                        ct.append(para_text(p).strip())
                    cells.append(' '.join(x for x in ct if x))
                out.append('| ' + ' | '.join(cells) + ' |')
            out.append('[TABLE END]\n')
        else:
            walk(child, out, depth + 1)


def main():
    with zipfile.ZipFile(SRC) as z:
        names = z.namelist()
        xml = z.read('word/document.xml')
    root = ET.fromstring(xml)
    body = root.find(f'{{{W}}}body')
    out = []
    walk(body, out)
    text = '\n'.join(out)
    text = re.sub(r'\n{3,}', '\n\n', text)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(text)
    print('OK', len(text))


if __name__ == '__main__':
    main()
