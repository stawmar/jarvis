#!/usr/bin/env python3
"""JARVIS v4 — konserwatywny skaner dowodów rzeczowych z DOCX po OCR.

Kontrakt z case_importer.py pozostaje bez zmian:
    [{"filename", "status", "data": {"source", "caseNumber", "internalNumber",
      "evidence": [{"number", "description"}]}, "ocrText", "approved", ...}]

Nie tworzy dowodów na podstawie nazwy pliku, liczby opakowań ani punktów
formularza. Wątpliwe fragmenty trafiają do reviewCandidates.
"""

import argparse
import json
import re
import unicodedata
from collections import OrderedDict
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

SOURCE_DIR = Path('/srv/data/nextcloud/data/marek/files/Raporty/JARVIS_TEST/Po_OCR')
OUTPUT_FILE = Path('document_scan.json')

CASE_NUMBER = re.compile(r'\b\d{4}\s*[-–]\s*\d+\s*\.?\s*Ds\.?\s*\d+\s*\.\s*\d{4}(?:\.[A-Za-z])?\b', re.I)
INTERNAL_NUMBER = re.compile(r'\b\d{4}/CHR/\d{2}\b', re.I)

# Rozpoznajemy tylko jawne etykiety dowodów, nie dowolne liczby.
NUMBER = r'\d{1,3}(?:[.,]\d{1,3})*(?:[A-Za-z]\d*)?'
EXPLICIT = re.compile(
    rf'(?<![\w/])(?:ślad\s*/\s*przedmiot|slad\s*/\s*przedmiot|'
    rf'ślad|slad|przedmiot|dow[oó]d\s+rzeczowy)'
    rf'\s*(?:nr\.?|numer|o\s+numerze)?\s*[:#-]?\s*'
    rf'(?P<number>{NUMBER})(?=$|[\s,;:()\]–—-])', re.I
)

# W wykazie można też spotkać same: „1 - susz”, „2A, proszek”.
# Granice pozycji ograniczają dopasowanie do faktycznej listy, a nie wag.
ENUMERATED = re.compile(
    rf'(?:(?<=^)|(?<=[\n;])|(?<=\s[-•*«])|(?<=\s[e]))'
    rf'\s*(?P<number>{NUMBER})\s*[).,:;–—-]\s*(?!\d)', re.I | re.M
)

# Rzeczownik/typ materiału daje pewność, że to opis fizycznego przedmiotu.
DESCRIPTIONS = re.compile(
    r'\b(?:susz\w*|roslin\w*|konopi\w*|krzak\w*|top\w*|'
    r'marihuan\w*|haszysz\w*|amfetamin\w*|metamfetamin\w*|'
    r'mdma|prosz\w*|sproszkow\w*|skrystalizowan\w*|krysztal\w*|'
    r'tabletk\w*|worecz\w*|zawiniat\w*|kopert\w*|opakowan\w*|'
    r'pudel\w*|torb\w*|fiolk\w*|bryl\w*|kapsul\w*|'
    r'substancj\w*\s+(?:koloru|barwy|proszkow\w*|skrystalizowan\w*))\b', re.I
)

PHYSICAL_HEADINGS = re.compile(
    r'\b(?:material\s+dowodowy|material\s+rzeczowy|'
    r'dowody\s+rzeczowe|wykaz\s+dowodow|wykaz\s+sladow|'
    r'wykaz\s+przedmiotow|spis\s+i\s+opis\s+rzeczy|'
    r'przedmioty\s+przekazane\s+do\s+badan)\b', re.I
)

# Zamknięcie listy nawet wewnątrz jednego ogromnego akapitu OCR.
# „kserokopię postanowienia” nie kończy wykazu, dlatego szukamy
# formalnych nagłówków dopiero ZA początkiem listy.
SECTION_END = re.compile(
    r'\b(?:z\s+powazaniem|z\s+wyrazami\s+szacunku|'
    r'oswiadczenie\s+zleceniobiorcy|oswiadczenie\s+zleceniodawcy|'
    r'postanowienie\s+o\s+dopuszczeniu|'
    r'postanowienie\s+w\s+przedmiocie|'
    r'odpis\s+postanowienia|'
    r'(?<!kserokopii\s)POSTANOWIENIE\b|'
    r'biuro\s+ekspertyz\s+sadowych\s+sp\.?\s*z\s*o\.?\s*o\.?|'
    r'regon\s*[:\-]|krs\s*[:\-])', re.I
)

PACKAGE = re.compile(
    r'\b(?:k\.?\s*bezp\.?|kopert\w*|worecz\w*|zawiniat\w*|'
    r'\bnr\s+[a-z]{2,5}[- ]?\d{5,})\b', re.I
)

PHYSICAL_NAME = re.compile(r'(?:\d+\s*x\s*)?(?:susz|amfa|dop|hasz|mdma)', re.I)
FORM_FIELDS = re.compile(
    r'\b(?:cena\s+netto|cena\s+brutto|kwota\s+podatku|'
    r'termin\s+platnosci|termin\s+wykonania|postepowanie\s+z\s+probka|'
    r'ustalenia\s+do\s+zlecenia|krajowego\s+rejestru\s+sadowego)\b', re.I
)


def norm(text):
    """Upraszcza zapis do dopasowań; oryginał pozostaje nienaruszony."""
    text = str(text).replace('ł', 'l').replace('Ł', 'L')
    return ''.join(
        c for c in unicodedata.normalize('NFKD', text)
        if not unicodedata.combining(c)
    ).lower()


def clean(text):
    """Usuwa jedynie nadmiar białych znaków."""
    return re.sub(r'\s+', ' ', str(text).replace('\xa0', ' ')).strip()


def read_docx(path):
    """Odczytuje DOCX w kolejności akapitów i tabel, nie gubiąc tabel."""
    doc = Document(path)
    chunks = []
    for element in doc.element.body.iterchildren():
        if element.tag == qn('w:p'):
            value = Paragraph(element, doc).text.strip()
            if value:
                chunks.append(value)
        elif element.tag == qn('w:tbl'):
            table = Table(element, doc)
            for row in table.rows:
                value = ' | '.join(clean(cell.text) for cell in row.cells)
                if value.strip(' |'):
                    chunks.append(value)
    return '\n'.join(chunks)


def source_of(text, filename):
    header = norm(text[:2500])
    if 'biuro ekspertyz sadowych' in header:
        return 'BES'
    if 'zespol lekarzy opiniujacych' in header:
        return 'ZLO'
    if filename.upper().startswith('BES'):
        return 'BES'
    if filename.upper().startswith('ZLO'):
        return 'ZLO'
    return None


def physical_description(text):
    flattened = norm(text)
    return bool(DESCRIPTIONS.search(flattened)) and not FORM_FIELDS.search(flattened)


def extract_section(text, source):
    """Wyodrębnia sam wykaz dowodów, również z JEDNEGO akapitu OCR.

    W dokumentach ZLO formalne pola 1–10 nie są wykazem dowodów.
    ZLO analizujemy tylko w polu materiału, bez pól cen i terminów.
    """
    normalized = norm(text)
    # W BES podstawą jest lista w piśmie przewodnim, nie pytania biegłego.
    # Nie szukamy dowodów w całym postanowieniu.
    if source != 'BES':
        return None

    # Pod uwagę bierzemy pierwszy wykaz materiału dowodowego.
    heading = PHYSICAL_HEADINGS.search(normalized)
    if heading is None:
        return None
    # Jeżeli pierwszy wykaz pojawia się dopiero bardzo głęboko w tekście,
    # prawdopodobnie należy do postanowienia, a nie pisma przewodniego.
    if heading.start() > 5000:
        return None

    start = heading.end()
    tail = text[start:start + 11000]
    terminator = SECTION_END.search(norm(tail))
    if terminator:
        tail = tail[:terminator.start()]
    return tail.strip(' \t\r\n:–—-')


def markers_in(section):
    """Zwraca wszystkie różne znaczniki pozycji (jawne + lista numerowana).

    Analizujemy POZYCJE W TEKŚCIE, nie linie DOCX. Dzięki temu 14 pozycji
    sklejonych w jeden akapit zostanie rozdzielonych na 14 opisów.
    """
    matches = []
    spans = []
    for m in EXPLICIT.finditer(section):
        matches.append((m.start(), m.end(), m.group('number'), True))
        spans.append((m.start(), m.end()))
    for m in ENUMERATED.finditer(section):
        # Nie traktujemy numeru z „przedmiot nr 1” jako drugiego dowodu.
        if any(left <= m.start() < right for left, right in spans):
            continue
        matches.append((m.start(), m.end(), m.group('number'), False))
    return sorted(matches, key=lambda item: (item[0], not item[3]))


def extract_evidence(text, source=None, filename=''):
    """Zwraca: pewne dowody, ostrzeżenia, kandydatów do weryfikacji."""
    evidence = OrderedDict()
    candidates = []
    warnings = []

    def candidate(value, reason, position=None):
        value = clean(value)[:350]
        if not value:
            return
        entry = {'line': text[:position].count('\n') + 1 if position is not None else None,
                 'text': value, 'reason': reason}
        if not any(x['text'] == entry['text'] and x['reason'] == reason for x in candidates):
            candidates.append(entry)

    section = extract_section(text, source)

    # ZLO: wyłącznie pole nr 3 formularza, przed polem nr 4.
    # Numerów ceny, podatku i terminów nie traktujemy jak dowodów.
    if section is None and source == 'ZLO':
        normalized = norm(text)
        heading = re.search(
            r'material\s+do\s+badan|material\s+for\s+examination',
            normalized
        )
        if heading:
            tail = normalized[heading.start():heading.start() + 1800]
            next_field = re.search(
                r'\b4\s*[.]\s*cena\s+netto|'
                r'\bnet\s+price|'
                r'\boswiadczenie\s+zleceniobiorcy',
                tail
            )
            if next_field:
                tail = tail[:next_field.start()]
            # Rzeczowy charakter materiału wynika z treści, nie z nazwy.
            if re.search(
                r'\b(?:susz\w*|prosz\w*|hasz\w*|amfetamin\w*|'
                r'konopi\w*|substancj\w*|tabletk\w*|narkotyk\w*)\b',
                tail
            ):
                section = tail

    if not section:
        if source == 'BES' and PHYSICAL_NAME.search(norm(filename)):
            warnings.append('Nazwa sugeruje dowody rzeczowe; brak pewnego wykazu w piśmie')
        return [], warnings, candidates

    markers = markers_in(section)

    # Jeżeli przed pierwszym rozpoznanym numerem jest opis przedmiotu,
    # zachowujemy go do ręcznej oceny. Nie nadajemy mu numeru na siłę.
    if markers:
        unnumbered_prefix = section[:markers[0][0]]
        # ZLO: sam opis pola «SUSZ» przed etykietą «ślad nr 1»
        # nie oznacza odrębnego dowodu i nie wymaga ostrzeżenia.
        if physical_description(unnumbered_prefix) and not (
            source == 'ZLO' and 'material do badan' in norm(unnumbered_prefix)
        ):
            candidate(
                unnumbered_prefix[-350:],
                'Opis materiału przed pierwszym rozpoznanym numerem'
            )

    for index, (start, end, number, explicit) in enumerate(markers):
        next_start = markers[index + 1][0] if index + 1 < len(markers) else len(section)
        description = clean(section[end:next_start]).strip(' ,;:|–—-')
        # Odrzucamy artefakt „; e” pozostały po znaczniku kolejnej pozycji.
        description = re.sub(r'(?:[;,.]\s*)?[e«•]\s*$', '', description).strip(' ;,')
        # Opis jednego przedmiotu nie może być całym późniejszym dokumentem.
        # Jeśli OCR łączy dwie pozycje bez poprawnego znacznika, nie zgadujemy.
        if len(description) > 450:
            candidate(section[start:min(len(section), start + 300)],
                      'Zbyt długi opis — możliwie sklejone pozycje')
            continue
        if not physical_description(description):
            if explicit:
                candidate(section[start:min(len(section), start + 250)],
                          'Jawny numer dowodu z nieczytelnym opisem')
            continue
        if number in evidence:
            if norm(evidence[number]['description']) != norm(description):
                candidate(section[start:min(len(section), start + 260)],
                          f'Powtórzony numer dowodu {number} z innym opisem')
                warnings.append(f'Powtórzony numer dowodu {number} z innym opisem — sprawdź OCR')
            continue
        evidence[number] = {'number': number, 'description': description}

    # Brak jednoznacznego numeru: opakowanie pozostaje kandydatem,
    # nigdy automatycznie tworzonym „dowodem 1”.
    if not markers and PACKAGE.search(norm(section)):
        for part in re.split(r'\s*;\s*|\s+(?=-\s+(?:e\s+)?(?:k\.?\s*bezp|kopert|worecz|zawiniat))', section):
            if PACKAGE.search(norm(part)) and physical_description(part):
                candidate(part, 'Opakowanie lub substancja bez jednoznacznego numeru')
    elif not markers and physical_description(section):
        candidate(section[:350], 'Opis materiału rzeczowego bez numeru dowodu')

    if candidates:
        warnings.append('Niejednoznaczne fragmenty OCR — sprawdź reviewCandidates')
    if not evidence and source == 'BES' and PHYSICAL_NAME.search(norm(filename)):
        warnings.append('Nazwa sugeruje dowody rzeczowe; brak pewnych pozycji')
    return list(evidence.values()), list(dict.fromkeys(warnings)), candidates


def extract_data(text, filename=''):
    """Wstecznie zgodne pola wymagane przez importer."""
    source = source_of(text, filename)
    case = CASE_NUMBER.search(text)
    internal = INTERNAL_NUMBER.search(text)
    evidence, _, _ = extract_evidence(text, source, filename)
    return {
        'source': source,
        'caseNumber': clean(case.group(0)) if case else None,
        'internalNumber': internal.group(0) if internal else None,
        'evidence': evidence,
    }


def main():
    parser = argparse.ArgumentParser(description='JARVIS — skaner dowodów rzeczowych')
    parser.add_argument('--source', type=Path, default=SOURCE_DIR)
    parser.add_argument('--output', type=Path, default=OUTPUT_FILE)
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    if not args.source.is_dir():
        parser.error(f'Nie istnieje katalog: {args.source}')

    records = []
    for file in sorted(args.source.glob('*.docx')):
        try:
            text = read_docx(file)
            data = extract_data(text, file.name)
            _, warnings, candidates = extract_evidence(text, data['source'], file.name)
            records.append({
                'filename': file.name,
                'status': 'OK',
                'data': data,
                'ocrText': text,
                'approved': False,
                'warnings': warnings,
                'reviewCandidates': candidates,
            })
            print(f"{file.name} | sprawa: {data['caseNumber']} | "
                  f"wew: {data['internalNumber']} | "
                  f"dowody: {len(data['evidence'])} | ostrzeżenia: {len(warnings)}")
            for warning in warnings:
                print('  !', warning)
            if args.debug:
                for item in data['evidence']:
                    print(f"  + {item['number']}: {item['description'][:180]}")
                for item in candidates[:12]:
                    print(f"  ? {item['reason']}: {item['text'][:200]}")
        except Exception as exc:
            records.append({'filename': file.name, 'status': 'ERROR', 'error': str(exc)})
            print(f'ERROR: {file.name}: {exc}')

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nPrzeanalizowano: {len(records)} dokumentów')
    print(f'Wynik zapisano: {args.output}')


if __name__ == '__main__':
    main()
