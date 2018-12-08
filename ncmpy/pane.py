#!/usr/bin/env python3

'''
pane module;
'''

from os.path import basename
from os.path import dirname
import curses
import locale
import mpd
import os
import threading
import time

from ncmpy import lrc
from ncmpy import ttplyrics
from ncmpy.config import conf
from ncmpy.keysym import code2name as c2n
from ncmpy.keysym import keysym as ks
from ncmpy.keysym import keysymgrp as ksg
from ncmpy.util import format_time
from ncmpy.util import get_tag

class Pane():

    '''
    the base class of all panes; each pane has a name, a window and a reference
    to the main controller;
    '''

    def __init__(self, name, win, ctrl):

        '''
        init this pane;

        ## params

        name:
        :   name of this pane;

        win:
        :   window of this pane;

        ctrl:
        :   the main controller;
        '''

        self.name = name
        self.win = win
        self.ctrl = ctrl

        self.mpc = self.ctrl.mpc
        self.ipc = self.ctrl.ipc
        self.height, self.width = self.win.getmaxyx()

    def fetch(self):

        '''
        fetch data;
        '''

        self.status = self.ctrl.status
        self.stats = self.ctrl.stats
        self.currentsong = self.ctrl.currentsong

    def round0(self):

        '''
        round 0;
        '''

        if self == self.ctrl.cpane:
            ##  current pane takes input char;
            self.ch = self.ctrl.ch
        else:
            ##  other panes take no input;
            self.ch = None

    def round1(self):

        '''
        round 1;
        '''

        pass

    def update(self):

        '''
        update window;
        '''

        pass

    def resize(self):

        '''
        resize window;
        '''

        pass

class BarPane(Pane):

    '''
    a bar pane has full width, one-line height and is put at a fixed position in
    the main window;
    '''

    def _resize(self, y, x):

        '''
        move and resize this bar pane;

        ## params

        y:int
        :   y position of new upper left corner;

        x:int
        :   x position of new upper left corner;
        '''

        self.win.resize(1, self.ctrl.width)
        self.height, self.width = self.win.getmaxyx()
        self.win.mvwin(y, x)

class BlockPane(Pane):

    '''
    a block pane has full width, multi-line height and is put at the center in
    the main window; different block panes may overlap; the topmost one accepts
    user input;
    '''

    def _resize(self, y, x):

        '''
        move and resize this block pane;

        ## params

        y:int
        :   y position of new upper left corner;

        x:int
        :   x position of new upper left corner;
        '''

        self.win.resize(self.ctrl.height - 4, self.ctrl.width)
        self.height, self.width = self.win.getmaxyx()
        self.win.mvwin(y, x)

    def resize(self):
        self._resize(2, 0)

class ScrollPane(BlockPane):

    '''
    a scroll pane is a special block pane which has scrollable content;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)

        ##  total number of lines;
        self.num = 0

        ##  beginning line number;
        self.beg = 0

    def line_down(self):
        self.beg = max(0, min(self.num - self.height, self.beg + 1))

    def line_up(self):
        self.beg = max(0, min(self.num - self.height, self.beg - 1))

    def page_down(self):
        self.beg = max(0, min(self.num - self.height, self.beg + self.height))

    def page_up(self):
        self.beg = max(0, min(self.num - self.height, self.beg - self.height))

    def locate(self, pos):
        self.beg = max(0, min(self.num - self.height, pos - self.height // 2))

class CursedPane(BlockPane):

    '''
    a cursed pane is a special block pane which has scrollable content and a
    cursor;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)

        ##  total number of lines;
        self.num = 0

        ##  beginning line number;
        self.beg = 0

        ##  selected line number;
        self.sel = 0

        ##  current line number;
        self.cur = 0

    def line_down(self):
        if self.sel < self.num - 1:
            self.sel += 1
            if self.sel - self.beg == self.height:
                self.beg += 1

    def line_up(self):
        if self.sel > 0:
            self.sel -= 1
            if self.sel - self.beg == -1:
                self.beg -= 1

    def page_down(self):
        if self.sel < self.num - self.height:
            self.sel += self.height
            self.beg = min(self.num - self.height, self.beg + self.height)
        else:
            self.sel = self.num - 1
            self.beg = max(0, self.num - self.height)

    def page_up(self):
        if self.sel < self.height:
            self.sel = 0
            self.beg = 0
        else:
            self.sel -= self.height
            self.beg = max(0, self.beg - self.height)

    def select_top(self):
        self.sel = self.beg

    def select_mid(self):
        self.sel = min(self.num - 1, self.beg + self.height // 2)

    def select_bot(self):
        self.sel = min(self.num - 1, self.beg + self.height - 1)

    def select_first(self):
        self.beg = 0
        self.sel = 0

    def select_last(self):
        self.beg = max(0, self.num - 1)
        self.sel = max(0, self.num - 1)

    def locate(self, pos):
        self.beg = max(0, pos - self.height // 2)
        self.sel = pos

    def clamp(self, pos):
        return max(0, min(self.num - 1, pos))

    def _resize(self, y, x):
        super()._resize(y, x)
        self.sel = min(self.beg + self.height - 1, self.sel)

    def search(self, pane_name, ch):
        if not (self.ctrl.search_kw and self.ctrl.search_dr): return

        dr = {
            ord('/') : + 1,
            ord('?') : - 1,
            ord('n') : + self.ctrl.search_dr,
            ord('N') : - self.ctrl.search_dr,
        }[ch]

        found = False
        for k in range(self.sel + dr, self.sel + dr + dr * len(self.items), dr):
            i = k % len(self.items)
            item = self.items[i]

            if pane_name in [ 'Queue', 'Search' ]:
                title = get_tag('title', item) or basename(item['file'])
            elif pane_name == 'Database':
                title = list(item.values())[0]
            elif pane_name == 'Artist-Album':
                if self._type in ['artist', 'album']:
                    title = item
                elif self._type == 'song':
                    title = get_tag('title', item) or basename(item['file'])

            if self.ctrl.search_kw in title:
                found = True
                if dr == 1 and i <= self.sel:
                    self.ipc['msg'] = 'search hit BOTTOM, continuing at TOP'
                elif dr == -1 and i >= self.sel:
                    self.ipc['msg'] = 'search hit TOP, continuing at BOTTOM'
                self.locate(i)
                break

        if not found:
            self.ipc['msg'] = 'Not found: {}'.format(self.ctrl.search_kw)

class MenuPane(BarPane):

    '''
    display pane name, play mode and volume;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)
        self.win.attron(curses.A_BOLD)

    def build_menu_str(self):
        title = self.ctrl.cpane.name
        mode = '{:5s}{:5s}{:5s}{:5s}'.format(
            '[con]' if int(self.status['consume']) else '',
            '[ran]' if int(self.status['random']) else '',
            '[rep]' if int(self.status['repeat']) else '',
            '[sin]' if int(self.status['single']) else '',
        )
        vol = 'Volume: {:3d}%'.format(int(self.status['volume']))

        return title + (mode + ' ' * 4 + vol).rjust(self.width - len(title))

    def update(self):
        ##  must use `insstr` instead of `addstr`, because `addstr` cannot draw
        ##  the last character (will raise an exception); this also applies to
        ##  other panes;
        self.win.erase()
        self.win.insstr(0, 0, self.build_menu_str())
        self.win.noutrefresh()

    def resize(self):
        self._resize(0, 0)

class LinePane(BarPane):

    '''
    display a horizontal line;
    '''

    def update(self):
        self.win.erase()
        self.win.insstr(0, 0, '-' * self.width)
        self.win.noutrefresh()

    def resize(self):
        self._resize(1, 0)

class ProgressPane(BarPane):

    '''
    display playback progress;
    '''

    def build_progress_str(self):
        state = self.status.get('state')
        if state == 'stop':
            return '-' * self.width
        else:
            elapsed, total = self.status.get('time').split(':')
            pos = int(int(elapsed) / int(total) * (self.width - 1))
            return '=' * pos + '0' + '-' * (self.width - pos - 1)

    def update(self):
        self.win.erase()
        self.win.insstr(0, 0, self.build_progress_str())
        self.win.noutrefresh()

    def resize(self):
        self._resize(self.ctrl.height - 2, 0)

class StatusPane(BarPane):

    '''
    display playback status (song title, elapsed and total time);
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)
        self.win.attron(curses.A_BOLD)

    def build_title_str(self):
        state = {
            'play'  : 'Playing',
            'stop'  : 'Stopped',
            'pause' : 'Paused',
        }[self.status.get('state')]
        song = self.currentsong
        title = song and (song.get('title') or basename(song.get('file'))) or ''
        return '{} > {}'.format(state, title)

    def build_tm_str(self):
        tm = self.status.get('time') or '0:0'
        elapsed, total = map(int, tm.split(':'))
        elapsed_mm, elapsed_ss = divmod(elapsed, 60)
        total_mm, total_ss = divmod(total, 60)
        return '[{}:{:02d} ~ {}:{:02d}]'.format(
            elapsed_mm, elapsed_ss, total_mm, total_ss)

    def update(self):
        ##  use two strs because it is difficult to calculate display length of
        ##  unicode characters;
        title = self.build_title_str()
        tm = self.build_tm_str()

        self.win.erase()
        self.win.insstr(0, 0, title)
        self.win.insstr(0, self.width - len(tm), tm)
        self.win.noutrefresh()

    def resize(self):
        self._resize(self.ctrl.height - 1, 0)

class MessagePane(BarPane):

    '''
    display message; get user input;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)
        self.msg = None
        self.timeout = 0

    def getstr(self, prompt):

        '''
        get user input with prompt;
        '''

        curses.nocbreak()
        curses.echo()
        curses.curs_set(1)
        self.win.move(0, 0)
        self.win.clrtoeol()
        self.win.addstr(f'{prompt}: ', curses.A_BOLD)
        s = self.win.getstr(0, len(prompt) + 2)
        curses.curs_set(0)
        curses.noecho()
        curses.cbreak()
        return s.decode()

    def update(self):
        msg = self.ipc.get('msg')
        if msg:
            self.msg = msg
            self.timeout = 10   ##  magic: dismiss msg after 10 updates;

        ##  todo: use a real timer;
        if self.timeout > 0:
            self.win.erase()
            self.win.insstr(0, 0, self.msg, curses.A_BOLD)
            self.win.noutrefresh()
            self.timeout -= 1

    def resize(self):
        self._resize(self.ctrl.height - 1, 0)

class HelpPane(ScrollPane):

    '''
    display help message;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)
        self.lines = [
            ('head', 'global'               , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.panehelp)       , 'help'                    ),
            ('item', c2n(ks.panequeue)      , 'queue'                   ),
            ('item', c2n(ks.panedatabase)   , 'database'                ),
            ('item', c2n(ks.panelyrics)     , 'lyrics'                  ),
            ('item', c2n(ks.paneartistalbum), 'artist-album'            ),
            ('item', c2n(ks.panesearch)     , 'search'                  ),
            ('item', c2n(ks.paneinfo)       , 'info'                    ),
            ('item', c2n(ks.paneoutput)     , 'output'                  ),
            ('void', ''                     , ''                        ),
            ('item', c2n(ks.quit)           , 'quit'                    ),
            ('void', ''                     , ''                        ),
            ('head', 'playback'             , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.play)           , 'play'                    ),
            ('item', c2n(ks.pause)          , 'pause'                   ),
            ('item', c2n(ks.stop)           , 'stop'                    ),
            ('item', c2n(ks.next)           , 'next song'               ),
            ('item', c2n(ks.prev)           , 'prev song'               ),
            ('void', ''                     , ''                        ),
            ('item', c2n(ks.consume)        , 'consume mode'            ),
            ('item', c2n(ks.random)         , 'random mode'             ),
            ('item', c2n(ks.repeat)         , 'repeat mode'             ),
            ('item', c2n(ks.single)         , 'single mode'             ),
            ('void', ''                     , ''                        ),
            ('item', c2n(ks.voldn)          , 'volume down'             ),
            ('item', c2n(ks.volup)          , 'volume up'               ),
            ('void', ''                     , ''                        ),
            ('item', c2n(ks.seekb)          , 'seek -1'                 ),
            ('item', c2n(ks.seekf)          , 'seek +1'                 ),
            ('item', c2n(ks.seekbp)         , 'seek -1%'                ),
            ('item', c2n(ks.seekfp)         , 'seek +1%'                ),
            ('void', ''                     , ''                        ),
            ('head', 'movement'             , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.linedn)         , 'move one line down'      ),
            ('item', c2n(ks.lineup)         , 'move one line up'        ),
            ('item', c2n(ks.pagedn)         , 'move one page down'      ),
            ('item', c2n(ks.pageup)         , 'move one page up'        ),
            ('item', c2n(ks.first)          , 'move to first'           ),
            ('item', c2n(ks.last)           , 'move to last'            ),
            ('item', c2n(ks.top)            , 'move to top of screen'   ),
            ('item', c2n(ks.mid)            , 'move to mid of screen'   ),
            ('item', c2n(ks.bot)            , 'move to bot of screen'   ),
            ('void', ''                     , ''                        ),
            ('item', c2n(ks.searchdn)       , 'search down'             ),
            ('item', c2n(ks.searchup)       , 'search up'               ),
            ('item', c2n(ks.searchnext)     , 'next match'              ),
            ('item', c2n(ks.searchprev)     , 'prev match'              ),
            ('void', ''                     , ''                        ),
            ('head', 'queue'                , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.play)           , 'play'                    ),
            ('item', c2n(ks.locate)         , 'locate current song'     ),
            ('item', c2n(ks.lock)           , 'toggle auto center'      ),
            ('item', c2n(ks.dblocate)       , 'locate song in database' ),
            ('void', ''                     , ''                        ),
            ('item', c2n(ks.unrate)         , 'unrate song'             ),
            ('item', c2n(ks.rate1)          , 'rate song as     *'      ),
            ('item', c2n(ks.rate2)          , 'rate song as    **'      ),
            ('item', c2n(ks.rate3)          , 'rate song as   ***'      ),
            ('item', c2n(ks.rate4)          , 'rate song as  ****'      ),
            ('item', c2n(ks.rate5)          , 'rate song as *****'      ),
            ('void', ''                     , ''                        ),
            ('item', c2n(ks.swapdn)         , 'move down selected song' ),
            ('item', c2n(ks.swapup)         , 'move up selected song'   ),
            ('item', c2n(ks.shuffle)        , 'shuffle queue'           ),
            ('item', c2n(ks.clear)          , 'clear queue'             ),
            ('item', c2n(ks.add)            , 'add songs from database' ),
            ('item', c2n(ks.delete)         , 'delete song from queue'  ),
            ('item', c2n(ks.savepl)         , 'save queue to playlist'  ),
            ('item', c2n(ks.loadpl)         , 'load queue from playlist'),
            ('void', ''                     , ''                        ),
            ('head', 'database'             , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.play)           , 'open dir|song|playlist'  ),
            ('item', c2n(ks.parent)         , 'go to parent dir'        ),
            ('item', c2n(ks.root)           , 'go to root dir'          ),
            ('item', c2n(ks.add)            , 'append song to queue'    ),
            ('item', c2n(ks.dblocate)       , 'locate song in queue'    ),
            ('item', c2n(ks.update)         , 'update database'         ),
            ('void', ''                     , ''                        ),
            ('head', 'lyrics'               , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.locate)         , 'center current line'     ),
            ('item', c2n(ks.lock)           , 'toggle auto center'      ),
            ('item', c2n(ks.savelyrics)     , 'save lyrics'             ),
            ('void', ''                     , ''                        ),
            ('head', 'artist-album'         , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.play)           , 'open artist|album|song'  ),
            ('item', c2n(ks.parent)         , 'go to parent level'      ),
            ('item', c2n(ks.root)           , 'go to root level'        ),
            ('item', c2n(ks.add)            , 'append song to queue'    ),
            ('item', c2n(ks.dblocate)       , 'locate song in queue'    ),
            ('void', ''                     , ''                        ),
            ('head', 'search'               , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.search)         , 'search {name}:{value}'   ),
            ('item', c2n(ks.play)           , 'play song'               ),
            ('item', c2n(ks.add)            , 'append song to queue'    ),
            ('item', c2n(ks.dblocate)       , 'locate song in queue'    ),
            ('void', ''                     , ''                        ),
            ('head', 'info'                 , ''                        ),
            ('line', ''                     , ''                        ),
            ('void', ''                     , ''                        ),
            ('head', 'output'               , ''                        ),
            ('line', ''                     , ''                        ),
            ('item', c2n(ks.toggle)         , 'toggle output'           ),
            ('void', ''                     , ''                        ),
        ]
        self.num = len(self.lines)

    def round0(self):
        super().round0()

        if self.ch == ord('j'):
            self.line_down()
        elif self.ch == ord('k'):
            self.line_up()
        elif self.ch == ord('f'):
            self.page_down()
        elif self.ch == ord('b'):
            self.page_up()

    def update(self):
        self.win.erase()
        for i in range(self.beg, min(self.num, self.beg + self.height)):
            l = self.lines[i]
            if l[0] == 'head':
                self.win.insstr(i - self.beg, 4, l[1], curses.A_BOLD)
            elif l[0] == 'line':
                self.win.attron(curses.A_BOLD)
                self.win.hline(i - self.beg, 4, '-', self.width - 8)
                self.win.attroff(curses.A_BOLD)
            elif l[0] == 'item':
                self.win.insstr(i - self.beg, 0, l[1].rjust(16) + ' : ' + l[2])
            elif l[0] == 'void':
                pass
        self.win.noutrefresh()

class QueuePane(CursedPane):

    '''
    display queue (current playlist);
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)

        ##  playlist version;
        self.pl_ver = -1

        ##  auto center current song;
        self.auto_center = False

    def fetch(self):
        super().fetch()

        ##  fetch playlist if playlist version is different;
        if self.pl_ver != int(self.status['playlist']):
            self.items = self.mpc.playlistinfo()
            self.num = len(self.items)
            self.beg = self.clamp(self.beg)
            self.sel = self.clamp(self.sel)

            for song in self.items:
                if conf.rate_song:
                    try:
                        sticker = self.mpc.sticker_get(
                            'song', song['file'], 'rating')
                        rating = int(sticker)
                    except mpd.CommandError:
                        rating = 0
                    finally:
                        song['rating'] = rating
                else:
                    song['rating'] = 0

            self.pl_ver = int(self.status['playlist'])

        ##  current song;
        self.cur = int(self.status.get('song', '0'))

    def round0(self):
        super().round0()

        if self.ch == ks.linedn:
            self.line_down()
        elif self.ch == ks.lineup:
            self.line_up()
        elif self.ch == ks.pagedn:
            self.page_down()
        elif self.ch == ks.pageup:
            self.page_up()
        elif self.ch == ks.top:
            self.select_top()
        elif self.ch == ks.mid:
            self.select_mid()
        elif self.ch == ks.bot:
            self.select_bot()
        elif self.ch == ks.first:
            self.select_first()
        elif self.ch == ks.last:
            self.select_last()
        elif self.ch == ks.locate:
            self.locate(self.cur)
        elif self.ch == ks.add:
            self.mpc.add('')
        elif self.ch == ks.clear:
            self.mpc.clear()
            self.num = self.beg = self.sel = self.cur = 0
        elif self.ch == ks.delete:
            if self.num > 0:
                self.ctrl.batch.append(
                    'deleteid({})'.format(self.items[self.sel]['id']))
                self.items.pop(self.sel)
                if self.sel < self.cur:
                    self.cur -= 1
                self.num -= 1
                self.beg = self.clamp(self.beg)
                self.sel = self.clamp(self.sel)
                self.cur = self.clamp(self.cur)
        elif self.ch == ks.swapdn:
            if self.sel + 1 < self.num:
                self.ctrl.batch.append(
                    'swap({}, {})'.format(self.sel, self.sel + 1))
                self.items[self.sel], self.items[self.sel + 1] = \
                        self.items[self.sel + 1], self.items[self.sel]
                if self.cur == self.sel:
                    self.cur += 1
                elif self.cur == self.sel + 1:
                    self.cur -= 1
                self.line_down()
        elif self.ch == ks.swapup:
            if self.sel > 0:
                self.ctrl.batch.append(
                    'swap({}, {})'.format(self.sel, self.sel - 1))
                self.items[self.sel - 1], self.items[self.sel] = \
                        self.items[self.sel], self.items[self.sel - 1]
                if self.cur == self.sel - 1:
                    self.cur += 1
                elif self.cur == self.sel:
                    self.cur -= 1
                self.line_up()
        elif self.ch == ks.shuffle:
            self.mpc.shuffle()
        elif self.ch == ks.play:
            self.mpc.playid(self.items[self.sel]['id'])
        elif self.ch in ksg.rate:
            if conf.rate_song:
                rating = {
                    ks.rate1: 1,
                    ks.rate2: 2,
                    ks.rate3: 3,
                    ks.rate4: 4,
                    ks.rate5: 5,
                }[self.ch]
                if 0 <= self.cur and self.cur < len(self.items):
                    song = self.items[self.cur]
                    self.mpc.sticker_set('song', song['file'], 'rating', rating)
                    song['rating'] = rating
        elif self.ch == ks.unrate:
            if conf.rate_song:
                if 0 <= self.cur and self.cur < len(self.items):
                    song = self.items[self.cur]
                    try:
                        self.mpc.sticker_delete('song', song['file'], 'rating')
                    except mpd.CommandError as e:
                        self.ipc['msg'] = str(e)
                    else:
                        song['rating'] = 0
        elif self.ch in ksg.search:
            self.search(self.name, self.ch)
        elif self.ch == ks.lock:
            self.auto_center = not self.auto_center
        elif self.ch == ks.dblocate:
            self.ipc['database-locate'] = self.items[self.sel]['file']

        ##  announce selected song;
        if self.num > 0:
            self.ipc['queue-selected'] = self.items[self.sel]

    def round1(self):
        super().round1()

        uri = self.ipc.get('queue-locate')
        if uri:
            for i in range(len(self.items)):
                if uri == self.items[i]['file']:
                    self.locate(i)
                    break
            else:
                self.ipc['msg'] = 'Not found in playlist'

        ##  auto center;
        if self.auto_center:
            self.locate(self.cur)

    def update(self):
        self.win.erase()
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            item = self.items[i]
            title = item.get('title') or basename(item['file'])
            rating = item.get('rating', 0)
            tm = format_time(item['time'])

            if i == self.cur:
                self.win.attron(curses.A_BOLD)
            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.addnstr(i - self.beg, 0, title, self.width - 18)
            self.win.addnstr(i - self.beg, self.width - 16, rating * '*', 5)
            self.win.insstr(i - self.beg, self.width - len(tm), tm)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
            if i == self.cur:
                self.win.attroff(curses.A_BOLD)
        self.win.noutrefresh()

class DatabasePane(CursedPane):

    '''
    display dirs, songs and playlists in database;

    todo: split database pane into song pane and playlist pane;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)

        ##  current dir;
        self.dir = ''
        self.items = self._list_items()

    def _list_items(self, keep_pos=False):
        '''
        list contents of current dir;

        this method is called when current dir changes, or new items are added
        or removed;

        ## params

        keep_pos:bool
        :   keep current position of display and selection;
        '''

        items = self.mpc.lsinfo(self.dir)
        items.insert(0, {'directory' : '..'})
        self.num = len(items)
        if keep_pos:
            self.beg = self.clamp(self.beg)
            self.sel = self.clamp(self.sel)
        else:
            self.beg = 0
            self.sel = 0
        return items

    def fetch(self):
        super().fetch()

        ##  database is changed;
        if 'database' in self.ipc.get('idle', []):
            self.dir = ''
            self.items = self._list_items()
            self.ipc['msg'] = 'Database updated.'

    def round0(self):
        super().round0()

        if self.ch == ks.linedn:
            self.line_down()
        elif self.ch == ks.lineup:
            self.line_up()
        elif self.ch == ks.pagedn:
            self.page_down()
        elif self.ch == ks.pageup:
            self.page_up()
        elif self.ch == ks.top:
            self.select_top()
        elif self.ch == ks.mid:
            self.select_mid()
        elif self.ch == ks.bot:
            self.select_bot()
        elif self.ch == ks.first:
            self.select_first()
        elif self.ch == ks.last:
            self.select_last()
        elif self.ch == ks.parent:
            old_dir = self.dir
            self.dir = dirname(self.dir)
            self.items = self._list_items()
            for i in range(self.num):
                if self.items[i].get('directory') == old_dir:
                    self.locate(i)
                    break
        elif self.ch == ks.root:
            self.dir = ''
            self.items = self._list_items()
        elif self.ch == ks.play:
            item = self.items[self.sel]
            if 'directory' in item:
                uri = item['directory']
                if uri == '..':
                    old_dir = self.dir
                    self.dir = dirname(self.dir)
                    self.items = self._list_items()
                    for i in range(self.num):
                        if self.items[i].get('directory') == old_dir:
                            self.locate(i)
                            break
                else:
                    self.dir = uri
                    self.items = self._list_items()
            elif 'file' in item:
                uri = item['file']
                songs = self.mpc.playlistfind('file', uri)
                if not songs:
                    self.mpc.add(uri)
                    songs = self.mpc.playlistfind('file', uri)
                self.mpc.playid(songs[0]['id'])
            elif 'playlist' in item:
                name = item['playlist']
                try:
                    self.mpc.load(name)
                except mpd.CommandError as e:
                    self.ipc['msg'] = str(e).rsplit('} ')[1]
                else:
                    self.ipc['msg'] = 'Playlist {} loaded'.format(name)
        elif self.ch == ks.add:
            item = self.items[self.sel]
            if 'directory' in item:
                uri = item['directory']
            else:
                uri = item['file']
            if uri == '..':
                self.mpc.add(dirname(self.dir))
            else:
                self.mpc.add(uri)
        elif self.ch == ks.delete:
            item = self.items[self.sel]
            if 'playlist' in item:
                name = item['playlist']
                try:
                    self.mpc.rm(name)
                except mpd.CommandError as e:
                    self.ipc['msg'] = str(e).rsplit('} ')[1]
                else:
                    self.ipc['msg'] = 'Playlist {} deleted'.format(name)
                    self.items = self._list_items(keep_pos=True)
        elif self.ch == ks.update:
            self.mpc.update()
        elif self.ch in ksg.search:
            self.search(self.name, self.ch)
        elif self.ch == ks.dblocate:
            ##  locate a song in queue;
            item = self.items[self.sel]
            if 'file' in item:
                self.ipc['queue-locate'] = item.get('file')
            else:
                self.ipc['msg'] = 'No song selected'

        ##  record selected song;
        self.ipc['database-selected'] = self.items[self.sel].get('file')

    def round1(self):
        super().round1()

        ##  if we need to locate a file in database, then rebuild item list
        ##  using item parent dir as display dir, and search for the file;
        uri = self.ipc.get('database-locate')
        if uri:
            self.dir = dirname(uri)
            self.items = self._list_items()
            for i in range(self.num):
                if self.items[i].get('file') == uri:
                    self.locate(i)
                    break
            else:
                self.ipc['msg'] = 'Not found in database'

        ##  if a playlist is saved, then rebuild item list;
        if self.ipc.get('playlist') == 'saved':
            self.items = self._list_items(keep_pos=True)

    def update(self):
        self.win.erase()
        for i in range(self.beg, min(self.num, self.beg + self.height)):
            item = self.items[i]
            if 'directory' in item:
                t, uri = 'directory', item['directory']
            elif 'file' in item:
                t, uri = 'file', item['file']
            elif 'playlist' in item:
                t, uri = 'playlist', item['playlist']

            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            if t == 'directory':
                self.win.attron(curses.color_pair(1) | curses.A_BOLD)
            elif t == 'playlist':
                self.win.attron(curses.color_pair(2) | curses.A_BOLD)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.insstr(i - self.beg, 0, basename(uri))
            if t == 'directory':
                self.win.attroff(curses.color_pair(1) | curses.A_BOLD)
            elif t == 'playlist':
                self.win.attroff(curses.color_pair(2) | curses.A_BOLD)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
        self.win.noutrefresh()

class LyricsPane(ScrollPane, threading.Thread):

    '''
    display lyrics;

    todo:
    '''

    def __init__(self, name, win, ctrl):
        ##  todo: there is a subtle bug: `theading.Thread` has a `name` field,
        ##  while `Pane` also has a `name` field; if we call `Pane.__init__`
        ##  first, then an exception is raised; this bug will be solved when we
        ##  later move `threading.Thread` out of a pane;
        threading.Thread.__init__(self, name='Lyrics')
        ScrollPane.__init__(self, name, win, ctrl)

        self.daemon = True

        # directory to save lyrics.
        # Make sure have write permission.
        self._lyrics_dir = conf.lyrics_dir

        if not os.path.isdir(self._lyrics_dir):
            os.makedirs(self._lyrics_dir)

        # new song, maintained by pane
        self._nsong = None
        # old song, maintained by worker
        self._osong = None
        # title of lyrics to fetch
        self._title = None
        # artist of lyrics to fetch
        self._artist = None
        # current lyrics, oneline str
        self._lyrics = '[00:00.00]Cannot fetch lyrics (No artist/title).'
        # current lyrics timestamp as lists, used by ctrl thread only
        self._ltimes = []
        # current lyrics text as lists, used by ctrl thread only
        self._ltexts = []
        # incicate lyrics state: 'local', 'net', 'saved' or False
        self._lyrics_state = False
        # condition variable for lyrics fetching and display
        self._cv = threading.Condition()

        # auto-center
        self.auto_center = True

    def _transtag(self, tag):
        '''Transform tag into format used by lrc engine.'''

        if tag is None:
            return None
        else:
            return tag.replace(' ', '').lower()

    def fetch(self):
        ScrollPane.fetch(self)

        song = self.currentsong

        # Do nothing if cannot acquire lock.
        if self._cv.acquire(blocking=False):
            self._nsong = song.get('file')
            # If currengsong changes, wake up worker.
            if self._nsong != self._osong:
                self._artist = song.get('artist')
                self._title = song.get('title')
                self._cv.notify()
            self._cv.release()

    def _save_lyrics(self):
        if self._artist and self._title and self._cv.acquire(blocking=False):
            with open(os.path.join(self._lyrics_dir, self._artist.replace('/', '_') + \
                    '-' + self._title.replace('/', '_') + '.lrc'), 'wt') as f:
                f.write(self._lyrics)
            self.ipc['msg'] = 'Lyrics {}-{}.lrc saved.'.format(self._artist, self._title)
            self._lyrics_state = 'saved'
            self._cv.release()
        else:
            self.ipc['msg'] = 'Lyrics saving failed.'

    def round0(self):
        super().round0()

        if self.ch == ord('j'):
            self.line_down()
        elif self.ch == ord('k'):
            self.line_up()
        elif self.ch == ord('f'):
            self.page_down()
        elif self.ch == ord('b'):
            self.page_up()
        elif self.ch == ord('l'):
            self.locate(self.cur)
        elif self.ch == ord('\''):
            self.auto_center = not self.auto_center
        elif self.ch == ord('K'):
            self._save_lyrics()

    def _parse_lrc(self, lyrics):
        '''Parse lrc lyrics into ltimes and ltexts.'''

        tags, tms = lrc.parse(lyrics)
        sorted_keys = sorted(tms.keys())
        ltimes = [int(i) for i in sorted_keys]
        ltexts = [tms.get(i) for i in sorted_keys]
        return ltimes, ltexts

    def current_line(self):
        '''Calculate line number of current progress.'''

        cur = 0
        tm = self.status.get('time')
        if tm:
            elapsed = int(tm.split(':')[0])
            while cur < self.num and self._ltimes[cur] <= elapsed:
                cur += 1
            cur -= 1
        return cur

    def round1(self):
        super().round1()

        # output 'Updating...' if cannot acquire lock
        if self._cv.acquire(blocking=0):
            # if worker reports lyrics fetched
            if self._lyrics_state in ['local', 'net']:
                # parse lrc (and copy lrc from shared mem to non-shared mem)
                self._ltimes, self._ltexts = self._parse_lrc(self._lyrics)
                self.num, self.beg = len(self._ltimes), 0

                # auto-save lyrics
                if self._lyrics_state == 'net' and self.num > 10:
                    self._save_lyrics()
                else:
                    self._lyrics_state = 'saved'

            self._cv.release()
        else:
            self._ltimes, self._ltexts = [0], ['Updating...']
            # set self.num and self.beg
            self.num, self.beg = 1, 0

        # set self.cur, the highlighted line
        self.cur = self.current_line()

        # auto center
        if self.auto_center:
            self.locate(self.cur)

    def update(self):
        self.win.erase()
        attr = curses.A_BOLD | curses.color_pair(3)
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            if i == self.cur:
                self.win.insstr(i - self.beg, 0, self._ltexts[i], attr)
            else:
                self.win.insstr(i - self.beg, 0, self._ltexts[i])
        self.win.noutrefresh()

    def run(self):
        self._cv.acquire()
        while True:
            # wait if currentsong doesn't change
            while self._nsong == self._osong:
                self._cv.wait()

            self._lyrics = '[00:00.00]Cannot fetch lyrics (No artist/title).'
            self._lyrics_state = 'local'

            # fetch lyrics if required information is provided
            if self._artist and self._title:
                # try to fetch from local lrc
                lyrics_file = os.path.join(self._lyrics_dir, self._artist.replace('/', '_') + \
                        '-' + self._title.replace('/', '_') + '.lrc')
                if os.path.isfile(lyrics_file):
                    with open(lyrics_file, 'rt') as f:
                        self._lyrics = f.read()
                    # inform round1: lyrics has been fetched
                    self._lyrics_state = 'local'
                # if local lrc doesn't exist, fetch from Internet
                else:
                    self._lyrics = ttplyrics.fetch_lyrics(self._transtag(self._artist), \
                            self._transtag(self._title))
                    # inform round1: lyrics has been fetched
                    self._lyrics_state = 'net'
            self._osong = self._nsong

class ArtistAlbumPane(CursedPane):

    '''
    display artists and albums;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)

        self._type = 'artist'
        self._artist = None
        self._album = None
        self.items = self._list_items()

    def _list_items(self):
        if self._type == 'artist':
            items = self.mpc.list('artist')
        elif self._type == 'album':
            items = self.mpc.list('album', self._artist) if self._artist else []
        elif self._type == 'song':
            items = self.mpc.find('album', self._album) if self._album else []

        self.num = len(items)
        self.beg = 0
        self.sel = 0
        return items

    def fetch(self):
        super().fetch()

        if 'database' in self.ipc.get('idle', []):
            self._type = 'artist'
            self.items = self._list_items()
            self.ipc['msg'] = 'Database updated.'

    def round0(self):
        super().round0()

        if self.ch == ks.linedn:
            self.line_down()
        elif self.ch == ks.lineup:
            self.line_up()
        elif self.ch == ks.pagedn:
            self.page_down()
        elif self.ch == ks.pageup:
            self.page_up()
        elif self.ch == ks.top:
            self.select_top()
        elif self.ch == ks.mid:
            self.select_mid()
        elif self.ch == ks.bot:
            self.select_bot()
        elif self.ch == ks.first:
            self.select_first()
        elif self.ch == ks.last:
            self.select_last()
        elif self.ch == ks.parent:
            if self._type == 'artist':
                pass
            elif self._type == 'album':
                self._type = 'artist'
                self.items = self._list_items()
                for i in range(self.num):
                    if self.items[i] == self._artist:
                        self.locate(i)
                        break
            elif self._type == 'song':
                self._type = 'album'
                self.items = self._list_items()
                for i in range(self.num):
                    if self.items[i] == self._album:
                        self.locate(i)
                        break
        elif self.ch == ks.root:
            self._type = 'artist'
            self.items = self._list_items()
        elif self.ch == ks.play:
            item = self.items[self.sel]
            if self._type == 'artist':
                self._artist = item
                self._type = 'album'
                self.items = self._list_items()
            elif self._type == 'album':
                self._album = item
                self._type = 'song'
                self.items = self._list_items()
            elif self._type == 'song':
                uri = item['file']
                songs = self.mpc.playlistfind('file', uri)
                if not songs:
                    self.mpc.add(uri)
                    songs = self.mpc.playlistfind('file', uri)
                self.mpc.playid(songs[0]['id'])
        elif self.ch == ks.add:
            item = self.items[self.sel]
            if self._type == 'artist':
                self.mpc.findadd('artist', item)
            elif self._type == 'album':
                self.mpc.findadd('album', item)
            elif self._type == 'song':
                self.mpc.add(item['file'])
        elif self.ch in ksg.search:
            self.search(self.name, self.ch)
        elif self.ch == ks.dblocate:
            ##  locate a song in queue;
            if self._type == 'song':
                item = self.items[self.sel]
                self.ipc['queue-locate'] = item.get('file')
            else:
                self.ipc['msg'] = 'No song selected'

    def update(self):
        self.win.erase()
        for i in range(self.beg, min(self.num, self.beg + self.height)):
            item = self.items[i]
            if self._type in [ 'artist', 'album' ]:
                title = item
            elif self._type == 'song':
                title = get_tag('title', item) or basename(item.get('file'))

            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            if self._type == 'artist':
                self.win.attron(curses.color_pair(1) | curses.A_BOLD)
            elif self._type == 'album':
                self.win.attron(curses.color_pair(2) | curses.A_BOLD)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.insstr(i - self.beg, 0, title)
            if self._type == 'artist':
                self.win.attroff(curses.color_pair(1) | curses.A_BOLD)
            elif self._type == 'album':
                self.win.attroff(curses.color_pair(2) | curses.A_BOLD)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
        self.win.noutrefresh()

class SearchPane(CursedPane):

    '''
    search in database;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)
        self.items = []

    def _list_items(self, search_kw):
        try:
            name, value = search_kw.split('=', 1)
            items = self.mpc.find(name, value) or []
            self.ipc['msg'] = 'Found {} results'.format(len(items))
        except:
            items = []
            self.ipc['msg'] = 'Search query format: {key}={value}'

        self.num = len(items)
        self.beg = 0
        self.sel = 0
        return items

    def round0(self):
        super().round0()

        if self.ch == ks.linedn:
            self.line_down()
        elif self.ch == ks.lineup:
            self.line_up()
        elif self.ch == ks.pagedn:
            self.page_down()
        elif self.ch == ks.pageup:
            self.page_up()
        elif self.ch == ks.top:
            self.select_top()
        elif self.ch == ks.mid:
            self.select_mid()
        elif self.ch == ks.bot:
            self.select_bot()
        elif self.ch == ks.first:
            self.select_first()
        elif self.ch == ks.last:
            self.select_last()
        elif self.ch == ks.search:
            self.items = self._list_items(
                self.ctrl.message_pane.getstr('Database Search'))
        elif self.ch == ks.play:
            item = self.items[self.sel]
            uri = item['file']
            songs = self.mpc.playlistfind('file', uri)
            if not songs:
                self.mpc.add(uri)
                songs = self.mpc.playlistfind('file', uri)
            self.mpc.playid(songs[0]['id'])
        elif self.ch == ks.add:
            item = self.items[self.sel]
            self.mpc.add(item['file'])
        elif self.ch in ksg.search:
            self.search(self.name, self.ch)
        elif self.ch == ks.dblocate:
            ##  locate a song in queue;
            if self.sel < self.num:
                item = self.items[self.sel]
                self.ipc['queue-locate'] = item.get('file')
            else:
                self.ipc['msg'] = 'No song selected'

    def update(self):
        self.win.erase()
        for i in range(self.beg, min(self.beg + self.height, self.num)):
            item = self.items[i]
            title = get_tag('title', item) or basename(item.get('file'))

            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.insstr(i - self.beg, 0, title)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
        self.win.noutrefresh()

class InfoPane(ScrollPane):

    '''
    display info about songs and database:

    -   currently playing;
    -   selected in queue;
    -   selected in database;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)

        ##  current playing;
        self._cp = {}
        ##  selected in queue;
        self._siq = {}
        ##  selected in database;
        self._sid = {}

        ##  database.sel's uri cache;
        self._dburi = None

        self.lines = [
            ['head', 'currently playing'    , ''],
            ['line', ''                     , ''],
            ['item', 'title'                , ''],
            ['item', 'artist'               , ''],
            ['item', 'album'                , ''],
            ['item', 'track'                , ''],
            ['item', 'genre'                , ''],
            ['item', 'date'                 , ''],
            ['item', 'time'                 , ''],
            ['item', 'file'                 , ''],
            ['void', ''                     , ''],
            ['head', 'selected in queue'    , ''],
            ['line', ''                     , ''],
            ['item', 'title'                , ''],
            ['item', 'artist'               , ''],
            ['item', 'album'                , ''],
            ['item', 'track'                , ''],
            ['item', 'genre'                , ''],
            ['item', 'date'                 , ''],
            ['item', 'time'                 , ''],
            ['item', 'file'                 , ''],
            ['void', ''                     , ''],
            ['head', 'selected in database' , ''],
            ['line', ''                     , ''],
            ['item', 'title'                , ''],
            ['item', 'artist'               , ''],
            ['item', 'album'                , ''],
            ['item', 'track'                , ''],
            ['item', 'genre'                , ''],
            ['item', 'date'                 , ''],
            ['item', 'time'                 , ''],
            ['item', 'file'                 , ''],
            ['void', ''                     , ''],
            ['head', 'mpd statistics'       , ''],
            ['line', ''                     , ''],
            ['item', 'songs'                , ''],
            ['item', 'artists'              , ''],
            ['item', 'albums'               , ''],
            ['item', 'uptime'               , ''],
            ['item', 'playtime'             , ''],
            ['item', 'db_playtime'          , ''],
            ['item', 'db_update'            , ''],
            ['void', ''                     , ''],
        ]
        self._song_keys = [
            'title', 'artist', 'album', 'track', 'genre', 'date', 'time',
            'file',
        ]
        self._stats_keys = [
            'songs', 'artists', 'albums', 'uptime', 'playtime', 'db_playtime',
            'db_update',
        ]

    def round0(self):
        super().round0()

        if self.ch == ks.linedn:
            self.line_down()
        elif self.ch == ks.lineup:
            self.line_up()
        elif self.ch == ks.pagedn:
            self.page_down()
        elif self.ch == ks.pageup:
            self.page_up()

    def round1(self):
        super().round1()

        ##  get info about songs;
        self._cp = self.currentsong
        self._siq = self.ipc.get('queue-selected', {})
        try:
            uri = self.ipc.get('database-selected', {})
            if uri and not self.ctrl.idle:
                self._sid = self.mpc.listallinfo(uri)[0]
        except (mpd.CommandError, IndexError):
            self._sid = {}

        ##  build lists;
        cp_list = [
            ['item', k, self._cp.get(k, '')] for k in self._song_keys
        ]
        siq_list = [
            ['item', k, self._siq.get(k, '')] for k in self._song_keys
        ]
        sid_list = [
            ['item', k, self._sid.get(k, '')] for k in self._song_keys
        ]
        stats_list = [
            ['item', k, self.stats.get(k, '')] for k in self._stats_keys
        ]

        for l in [ cp_list, siq_list, sid_list ]:
            for i in range(6):
                ##  if tag value is list, convert to str;
                if not isinstance(l[i][2], str):
                    l[i][2] = ', '.join(l[i][2])
            ##  format time;
            l[6][2] = format_time(l[6][2])

        for l in [ stats_list ]:
            for i in range(3, 6):
                ##  format time;
                l[i][2] = format_time(l[i][2])
            ##  format time;
            l[6][2] = time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(int(l[6][2])))

        ##  merge into ctrl list;
        self.lines[2:10] = cp_list
        self.lines[13:21] = siq_list
        self.lines[24:32] = sid_list
        self.lines[35:42] = stats_list

        self.lines_d = self.lines[:]
        for k in [ 31, 20, 9 ]:
            ##  breakup file paths;
            self.lines_d[k:k+1] = [
                [ 'item', '', '/' + i ] for i in self.lines[k][2].split('/')
            ]
            ##  strip leading slash;
            self.lines_d[k] = [ 'item', 'file', self.lines_d[k][2][1:] ]

        self.num = len(self.lines_d)

    def update(self):
        self.win.erase()
        for i in range(self.beg, min(self.num, self.beg + self.height)):
            l = self.lines_d[i]
            if l[0] == 'head':
                self.win.insstr(i - self.beg, 4, l[1], curses.A_BOLD)
            elif l[0] == 'line':
                self.win.attron(curses.A_BOLD)
                self.win.hline(i - self.beg, 4, '-', self.width - 8)
                self.win.attroff(curses.A_BOLD)
            elif l[0] == 'item':
                self.win.insstr(i - self.beg, 0, l[1].rjust(16) + ' : ' + l[2])
            elif l[0] == 'void':
                pass
        self.win.noutrefresh()

class OutputPane(CursedPane):

    '''
    display outputs;
    '''

    def __init__(self, name, win, ctrl):
        super().__init__(name, win, ctrl)
        self.outputs = []

    def fetch(self):
        super().fetch()

        self.outputs = self.mpc.outputs()
        self.num = len(self.outputs)
        self.beg = self.clamp(self.beg)
        self.sel = self.clamp(self.sel)

    def round0(self):
        super().round0()

        if self.ch == ks.linedn:
            self.line_down()
        elif self.ch == ks.lineup:
            self.line_up()
        elif self.ch == ks.pagedn:
            self.page_down()
        elif self.ch == ks.pageup:
            self.page_up()
        elif self.ch == ks.toggle:
            output = self.outputs[self.sel]
            output_id = int(output['outputid'])
            output_enabled = int(output['outputenabled'])
            if output_enabled:
                self.mpc.disableoutput(output_id)
                self.outputs[self.sel]['outputenabled'] = '0'
            else:
                self.mpc.enableoutput(output_id)
                self.outputs[self.sel]['outputenabled'] = '1'

    def update(self):
        self.win.erase()
        for i in range(self.beg, min(self.num, self.beg + self.height)):
            item = self.outputs[i]
            if i == self.sel:
                self.win.attron(curses.A_REVERSE)
            enabled = '[{}]'.format('o' if int(item['outputenabled']) else 'x')
            name = item['outputname']
            item_str = '{} {}'.format(enabled, name)
            self.win.hline(i - self.beg, 0, ' ', self.width)
            self.win.insstr(i - self.beg, 0, item_str)
            if i == self.sel:
                self.win.attroff(curses.A_REVERSE)
        self.win.noutrefresh()

