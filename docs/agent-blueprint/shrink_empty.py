"""Shrink empty paragraphs so they can't spill onto a new page.

docx-js closes every intermediate section with an empty paragraph that carries
the sectPr, sized by the 10pt default. Pages then pushes it onto its own page
whenever the section's last page is nearly full. Empty spacer paragraphs have
the same 10pt line. This gives every run-less paragraph a 1pt mark, and the
section-closing ones zero spacing and an exact 1pt line. Paragraphs with text
are not touched.
"""
import re
import sys
import zipfile

src, dst = sys.argv[1], sys.argv[2]
TINY_RPR = '<w:rPr><w:sz w:val="2"/><w:szCs w:val="2"/></w:rPr>'
TINY_SPACING = '<w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/>'
PARA = re.compile(r'<w:p(?=[ >])(?:(?!<w:p[ >]).)*?</w:p>', re.S)
counts = {"empty": 0, "sectPr": 0}


def fix(m):
    p = m.group(0)
    if re.search(r'<w:r[ >]', p) or '<w:drawing' in p:
        return p                                   # has content: leave alone
    counts["empty"] += 1
    has_sect = '<w:sectPr' in p
    if has_sect:
        counts["sectPr"] += 1
    if '<w:pPr>' not in p and '<w:pPr ' not in p:
        return re.sub(r'^(<w:p(?: [^>]*)?>)', r'\1<w:pPr>' + TINY_RPR + '</w:pPr>', p, count=1)
    if '<w:rPr>' in p.split('<w:sectPr')[0]:
        return p                                   # already has mark formatting
    insert = (TINY_SPACING if has_sect and '<w:spacing' not in p else '') + TINY_RPR
    if has_sect:
        return p.replace('<w:sectPr', insert + '<w:sectPr', 1)
    return p.replace('</w:pPr>', insert + '</w:pPr>', 1)


with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == 'word/document.xml':
            data = PARA.sub(fix, data.decode('utf8')).encode('utf8')
        zout.writestr(item, data)
print(f"shrunk {counts['empty']} empty paragraphs ({counts['sectPr']} section closers)")
