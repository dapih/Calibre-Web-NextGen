"""Compile private Windows catalogs with correct untranslated plural fallback."""
from pathlib import Path
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'cps' / 'translations'
DESTINATION = ROOT / '.m0' / 'translation-repaired'


def main():
    count = omitted = 0
    for source in sorted(SOURCE.glob('*/LC_MESSAGES/messages.po')):
        with source.open('rb') as stream:
            catalog = read_po(stream, locale=source.parents[1].name)
        for message in list(catalog):
            # Babel otherwise writes the English singular under a locale's
            # single plural index, so an untranslated count of two says "day".
            if isinstance(message.id, tuple) and not any(message.string or ()):
                catalog.delete(message.id)
                omitted += 1
        target = DESTINATION / source.relative_to(SOURCE).with_suffix('.mo')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('wb') as stream:
            write_mo(stream, catalog, use_fuzzy=False)
        count += 1
    if not count:
        raise ValueError('no translation catalogs found')
    print(f'Compiled {count} private catalogs; {omitted} untranslated plurals use source fallback.')


if __name__ == '__main__':
    main()
