#!/usr/bin/env python3

"""Collection of maintenance tools for XMage.

Usage:
  xmage-maintenance [options] change-set-code <xmage_set_dir> <new_set_code>
  xmage-maintenance [options] full-spoiler <set_code> <spoiler_url>
  xmage-maintenance [options] implemented <card_name> [<set_code>]
  xmage-maintenance [options] oracle-update <set_code>
  xmage-maintenance [options] total
  xmage-maintenance -h | --help

Options:
  -h, --help  Print this message and exit.
  --patch     When used with the `oracle-update` subcommand, only copy the cards section.
  --pull      Pull master before performing maintenance.
  --stdout    Print to stdout instead of copying to clipboard.
  --verbose   Print progress updates while performing maintenance.
"""

import sys

import collections
import docopt
import html.parser
import io
import itertools
import mtgjson
import os
import pathlib
import re
import requests
import subprocess

MASTER = os.environ.get('XMAGE_MASTER', pathlib.Path('/opt/git/github.com/magefree/mage/master'))
STAGE = os.environ.get('XMAGE_STAGE', pathlib.Path('/opt/git/github.com/fenhl/mage/stage'))

OPTIONS = {
    'stdout': False,
    'verbose': False
}

class FullSpoilerParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.div_found = False
        self.card_images = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div' and attrs.get('class') in ('resizing-cig', 'rtecenter'):
            self.div_found = True
        elif tag == 'img':
            self.handle_startendtag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'div':
            self.div_found = False

    def handle_startendtag(self, tag, attrs):
        attrs = dict(attrs)
        if self.div_found and tag == 'img' and 'src' in attrs and 'alt' in attrs:
            normalized_card_name = re.sub('’', "'", attrs['alt'])
            self.card_images[normalized_card_name] = attrs['src']

def copy(text):
    if OPTIONS['stdout']:
        print(text)
    else:
        subprocess.run(['pbcopy'], input=text.encode('utf-8'))

def implemented(name, expansion=None, *, class_name=None, set_dirs_cache=None):
    if expansion is None:
        if set_dirs_cache is None:
            set_dirs_cache = set_dirs()
        search_set_dirs = set_dirs_cache.values()
    elif isinstance(expansion, str):
        if set_dirs_cache is None:
            set_dirs_cache = set_dirs()
        search_set_dirs = [set_dirs_cache[expansion]]
    elif isinstance(expansion, pathlib.Path):
        search_set_dirs = [expansion]
    else:
        raise TypeError('Unsupported expansion type: {}'.format(type(expansion)))
    if class_name is None:
        candidates = itertools.chain.from_iterable(set_dir.iterdir() for set_dir in search_set_dirs)
    else:
        candidates = (set_dir / '{}.java'.format(class_name) for set_dir in search_set_dirs)
    for card in candidates:
        if card.is_dir():
            continue
        with card.open() as f:
            text = f.read()
        for line in text.split('\n'):
            if name in ('Plains', 'Island', 'Swamp', 'Mountain', 'Forest'):
                match = re.match('public class ([0-9A-Za-z]+) extends mage\\.cards\\.basiclands\\.{}'.format(name), line)
                if match:
                    return True if class_name is None else (True, match.group(1))
            match = re.match('        super\\(ownerId, [0-9]+, "(.+?)",', line)
            if match and (match.group(1) == name or class_name is not None):
                return True if class_name is None else (True, match.group(1))
            match = re.match('public class ([0-9A-Za-z]+) extends mage\\.sets\\.([0-9a-z]+)\\.([0-9A-Za-z]+) \\{', line)
            if match:
                if class_name is not None and class_name != match.group(1):
                    break
                if set_dirs_cache is None:
                    set_dirs_cache = set_dirs()
                found, super_name = implemented(name, expansion=MASTER / 'Mage.Sets' / 'src' / 'mage' / 'sets' / match.group(2), class_name=match.group(3), set_dirs_cache=set_dirs_cache)
                if super_name == name:
                    return found if class_name is None else (found, super_name)
                else:
                    break
    return False if class_name is None else (False, None)

def markdown_card_link(name, set_code, db=None):
    if db is None:
        db = mtgjson.CardDb.from_url()
    card = db.sets[set_code].cards_by_name[name]
    return '[{}](https://mtg.wtf/card/{}/{})'.format(name, set_code.lower(), card.number)

def set_dirs():
    result = {}
    total = sum(1 for f in (MASTER / 'Mage.Sets' / 'src' / 'mage' / 'sets').iterdir() if f.is_dir())
    for i, set_dir in enumerate(f for f in (MASTER / 'Mage.Sets' / 'src' / 'mage' / 'sets').iterdir() if f.is_dir()):
        if OPTIONS['verbose']:
            progress = int(5 * i / total)
            print('\r[{}{}] caching XMage set directories'.format('=' * progress, '.' * (4 - progress)), end='', flush=True)
        for card in set_dir.iterdir():
            if card.is_dir():
                continue
            with card.open() as f:
                text = f.read()
            for line in text.split('\n'):
                match = re.fullmatch('        this\\.expansionSetCode = "([0-9A-Z]+)";', line)
                if match:
                    if match.group(1) in result and result[match.group(1)] != set_dir:
                        raise ValueError('Set {} has multiple directories: {} and {}'.format(match.group(1), result[match.group(1)], set_dir))
                    result[match.group(1)] = set_dir
                    break
    if OPTIONS['verbose']:
        print('\r[ ok ]')
    return result

if __name__ == '__main__':
    arguments = docopt.docopt(__doc__)
    if arguments['--verbose']:
        import blessings

        term = blessings.Terminal()
        OPTIONS['verbose'] = True
    if arguments['--stdout']:
        OPTIONS['stdout'] = True
    if arguments['--pull']:
        subprocess.run(['git', 'pull'], cwd=str(MASTER))
    if arguments['change-set-code']:
        new_code = arguments['<new_set_code>']
        base_dir = STAGE / 'Mage.Sets' / 'src' / 'mage' / 'sets'
        set_dir = base_dir / arguments['<xmage_set_dir>']
        for card in set_dir.iterdir():
            with card.open() as f:
                text = f.read()
            lines = text.split('\n')
            for i in range(len(lines)):
                if re.fullmatch('        this\\.expansionSetCode = "[0-9A-Z]+";', lines[i]):
                    lines[i] = re.sub('"[0-9A-Z]+"', '"{}"'.format(new_code), lines[i])
                    break
            text = '\n'.join(lines)
            with card.open('w') as f:
                f.write(text)
    elif arguments['full-spoiler']:
        if OPTIONS['verbose']:
            print('[....] downloading MTG JSON', end='', flush=True)
        db = mtgjson.CardDb.from_url()
        if OPTIONS['verbose']:
            print('\r[ ok ]')
            print('[....] parsing full spoiler', end='', flush=True)
        full_spoiler = requests.get(arguments['<spoiler_url>']).text
        if OPTIONS['verbose']:
            print('\r[====]', end='', flush=True)
        full_spoiler_parser = FullSpoilerParser()
        full_spoiler_parser.feed(full_spoiler)
        card_images = full_spoiler_parser.card_images
        if OPTIONS['verbose']:
            print('\r[ ok ]')
        reprints = []
        new_cards = []
        num_cards = len(card_images)
        set_dirs_cache = set_dirs()
        for i, (name, image) in enumerate(sorted(card_images.items())):
            if OPTIONS['verbose']:
                progress = int(5 * i / num_cards)
                print('\r[{}{}] checking for implemented cards'.format('=' * progress, '.' * (4 - progress)), end='', flush=True)
            (reprints if name in db.cards_by_name else new_cards).append('- [{}] [{}]({})'.format('x' if implemented(name, arguments['<set_code>'], set_dirs_cache=set_dirs_cache) else ' ', name, image))
        if OPTIONS['verbose']:
            print('\r[ ok ]')
        if OPTIONS['stdout']:
            print('[ ** ] reprints')
        copy('\n'.join(reprints))
        if OPTIONS['stdout']:
            print('[ ** ] new cards')
        else:
            input('[ ** ] reprints copied to clipboard, press return to copy new cards')
        copy('\n'.join(new_cards))
        if not OPTIONS['stdout']:
            print('[ ** ] new cards copied to clipboard')
    elif arguments['implemented']:
        if implemented(arguments['<card_name>'], expansion=arguments['<set_code>']):
            if OPTIONS['verbose']:
                if arguments['<set_code>'] is None:
                    print('[ ok ] {}'.format(arguments['<card_name>']))
                else:
                    print('[ ok ] ({}) {}'.format(arguments['<set_code>'], arguments['<card_name>']))
            sys.exit()
        else:
            if OPTIONS['verbose']:
                print('[FAIL] ({}) {}'.format(arguments['<set_code>'], arguments['<card_name>']))
            sys.exit(1)
    elif arguments['oracle-update']:
        set_code = arguments['<set_code>']
        if OPTIONS['verbose']:
            print('[....] downloading MTG JSON', end='', flush=True)
        db = mtgjson.CardDb.from_url(mtgjson.ALL_SETS_X_ZIP_URL)
        if OPTIONS['verbose']:
            print('\r[ ok ]')
        reprints = []
        new_cards = []
        num_cards = len(db.sets[set_code].cards_by_name.items())
        set_dirs_cache = set_dirs()
        for i, (name, card) in enumerate(sorted(db.sets[set_code].cards_by_name.items())):
            if OPTIONS['verbose']:
                progress = int(5 * i / num_cards)
                print('\r[{}{}] checking for implemented cards'.format('=' * progress, '.' * (4 - progress)), end='', flush=True)
            (reprints if len(card.printings) > 1 else new_cards).append('- [{}] {}'.format('x' if implemented(name, set_code, set_dirs_cache=set_dirs_cache) else ' ', markdown_card_link(name, set_code, db=db)))
        if OPTIONS['verbose']:
            print('\r[ ok ]')
        copy(('' if arguments['--patch'] else """\
# Rules

The following rules changes from {set_code} may be relevant for XMage:

**TODO**

# Oracle

In {set_code}, there have been the following Oracle changes which will have to be implemented. Functional errata are marked in boldface, and unimplemented cards are omitted.

## Multiple cards

**TODO**

## Single card

**TODO**

""".format(set_code=set_code)) + """# Cards

The following cards have been printed in {set_code} and will have to be implemented.

## Reprints

{reprints}

## New cards

{new_cards}
""".format(reprints='\n'.join(reprints), new_cards='\n'.join(new_cards), set_code=set_code))
        print('[ ** ] text copied to clipboard')
    elif arguments['total']:
        cards = collections.defaultdict(lambda: 0)
        for set_dir in (MASTER / 'Mage.Sets' / 'src' / 'mage' / 'sets').iterdir():
            if not set_dir.is_dir():
                continue
            for card in set_dir.iterdir():
                if card.is_dir():
                    continue
                cards[card.name] += 1
        print('{} unique, {} total'.format(len(cards), sum(cards.values())))
    else:
        sys.exit('xmage-maintenance: subcommand not implemented')
