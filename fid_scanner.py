"""JARVIS: read FID labels and THC/AMF concentration tables from legacy Word .doc.

Does not extract or modify chromatogram images. Does not infer DOP from missing FID.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

SOURCE_DIR = Path('/srv/data/nextcloud/data/marek/files/Raporty/JARVIS_TEST/FID')
OUTPUT_FILE = Path('cases/fid_scan.json')

IDENTIFIER = re.compile(
    r'\bBES\s+(?P<prefix>\d{4}-\d+)\s*\.?\s*Ds\s*\.?\s*'
    r'(?P<number>\d+)\s*[,\.]\s*(?P<year>\d{4})'
    r'(?:\s*#\s*(?P<sample>\d+(?:[,\.]\d+)*))?',
    re.IGNORECASE,
)
PERCENT = re.compile(r'^\d+(?:[,\.]\d+)?$')


def normalize_identifier(raw):
    m = IDENTIFIER.search(raw)
    if not m:
        return None
    return {
        'caseNumber': f"{m['prefix']}.Ds.{m['number']}.{m['year']}",
        'evidenceNumber': m['sample'].replace(',', '.') if m['sample'] else None,
        'fidIdentifier': m.group(0).strip(),
    }


def read_doc(path):
    if not shutil.which('antiword'):
        raise RuntimeError('Brak antiword: zainstaluj pakiet antiword na NAS-ie.')
    result = subprocess.run(
        ['antiword', '-w', '160', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        check=True,
    )
    return result.stdout


def parse_fid(content, filename):
    chromatograms = []
    concentrations = []
    section = None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Antiword prints Word tables as |label|value| rows.
        if stripped.startswith('|'):
            cells = [cell.strip() for cell in stripped.strip('|').split('|')]
            if any(cell.upper() == '%THC' for cell in cells):
                section = 'THC'
                continue
            if any(cell.upper() == '%AMF' for cell in cells):
                section = 'AMF'
                continue
            if section and len(cells) >= 2:
                identifier = normalize_identifier(cells[0])
                percent = cells[1].replace(' ', '')
                if identifier and PERCENT.fullmatch(percent):
                    concentrations.append({
                        **identifier,
                        'substance': section,
                        'concentrationPercent': percent.replace(',', '.'),
                        'sourceFile': filename,
                        'approved': False,
                    })
            continue

        # After the concentration tables, identifiers are not chromatogram titles.
        if section is None:
            identifier = normalize_identifier(stripped)
            if identifier:
                chromatograms.append({
                    **identifier,
                    'sourceFile': filename,
                    'approved': False,
                })

    return chromatograms, concentrations


def main():
    files = sorted(SOURCE_DIR.glob('*.doc'))
    if not files:
        raise SystemExit(f'Brak plików .doc w {SOURCE_DIR}')

    all_chromatograms = []
    all_concentrations = []
    for path in files:
        chromatograms, concentrations = parse_fid(read_doc(path), path.name)
        all_chromatograms.extend(chromatograms)
        all_concentrations.extend(concentrations)
        print(f'{path.name}: chromatogramy={len(chromatograms)}, stężenia={len(concentrations)}')

    result = {
        'chromatograms': all_chromatograms,
        'concentrations': all_concentrations,
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'\nŁącznie: {len(all_chromatograms)} chromatogramów, '
          f'{len(all_concentrations)} stężeń')
    print(f'Zapisano: {OUTPUT_FILE}')
    print('\nWyniki 4181-4.Ds.1890.2026:')
    for row in all_concentrations:
        if row['caseNumber'] == '4181-4.Ds.1890.2026':
            print(f"  dowód {row['evidenceNumber']}: {row['substance']} "
                  f"{row['concentrationPercent']}%")


if __name__ == '__main__':
    main()