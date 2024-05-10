""" EDMC plugin for copying next system in route to clipboard. """
import _thread
import logging
import os
import math
import csv
from collections import OrderedDict
import tkinter as tk
import tkinter.messagebox
import tkinter.filedialog
from datetime import datetime, timezone, timedelta
from tkinter import ttk
from enum import Enum
from typing import Optional, Dict, Any

import timeout_session
import myNotebook as nb
from config import config
from config import appname


_plugin_name = os.path.basename(os.path.dirname(__file__))
_logger = logging.getLogger(f'{appname}.{_plugin_name}')

_VERSION = 'dev'


def _ebgs_fetch(path, params):
    """ Fetch data and all pages from Elite BGS.

        Check out https://elitebgs.app/ebgs/docs/V5/
    """
    url = f'https://elitebgs.app/api/ebgs/v5/{path}'
    session = timeout_session.new_session()
    items = []
    while True:
        response = session.get(url, params=params, timeout=60).json()
        items.extend(response['docs'])
        next_page = response['nextPage']
        params['page'] = next_page
        if next_page is None:
            break
    return items


def _ebgs_fetch_factions(faction_name):
    """ Fetch faction information for given faction name. """
    return _ebgs_fetch('factions', {
        'name': faction_name,
        'systemDetails': 'true'
    })


class _PluginConfigs(Enum):
    """ Plugin configuration. """

    FACTION_NAME = 'routeplanner_faction_name'
    MIN_AGE = 'routeplanner_min_age'

    def get_str(self):
        """ Return string setting value. """
        return config.get_str(self.value)

    def get_int(self, default):
        """ Return integer setting value. """
        return config.get_int(self.value, default=default)

    def set(self, config_value):
        """ Set new value for setting. """
        config.set(self.value, config_value)


class _FactionPresence:

    def __init__(self, name, updated_at, location):
        self.name = name
        self.updated_at = datetime.fromisoformat(updated_at)
        self.age = datetime.now(timezone.utc) - self.updated_at
        (self.x, self.y, self.z) = location

    @property
    def nice_age(self):
        """ Age in human readable form. """
        minute = 60
        hour = minute * 60
        if self.age.days > 1:
            return f'{self.age.days} days'
        if self.age.total_seconds() // hour > 1:
            return f'{self.age.total_seconds() // hour} hours'
        if self.age.total_seconds() // minute > 1:
            return f'{self.age.total_seconds() // minute} minutes'
        return f'{self.age.total_seconds()}s'

    def distance_to(self, target):
        """ Return distance to target. """
        return math.sqrt(
            (target.x - self.x) ** 2 +
            (target.y - self.y) ** 2 +
            (target.z - self.z) ** 2)


class _PluginPrefs:
    """ Plugin preferences. """

    def __init__(self):
        self.__faction_name_var = tk.StringVar(value=_PluginConfigs.FACTION_NAME.get_str())
        self.__min_age_var = tk.IntVar(value=_PluginConfigs.MIN_AGE.get_int(2))
        self.__text_frame = None
        self.systems = OrderedDict()
        self.start_system = None

    def create_frame(self, parent: nb.Notebook):
        """ Create and return preferences frame. """
        padx = 10
        pady = 4
        boxy = 2

        frame = nb.Frame(parent)
        frame.columnconfigure(0, weight=0)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=0)
        frame.rowconfigure(0, weight=0)
        frame.rowconfigure(1, weight=0)
        frame.rowconfigure(2, weight=0)
        frame.rowconfigure(3, weight=1)

        label = nb.Label(frame, text='Faction name:')
        label.grid(column=0, row=0, padx=padx, pady=pady, sticky=tk.W)
        entry = nb.Entry(frame, takefocus=False, textvariable=self.__faction_name_var)
        entry.grid(column=1, row=0, padx=padx, pady=boxy, sticky=tk.EW)
        load_button = ttk.Button(frame, text='Load', command=self.__on_load_faction_systems)
        load_button.grid(column=2, row=0, padx=padx, sticky=tk.EW)

        label = nb.Label(frame, text='Minimum age (hours):')
        label.grid(column=0, row=1, padx=padx, pady=pady, sticky=tk.W)
        entry = nb.Entry(frame, takefocus=False, textvariable=self.__min_age_var)
        entry.grid(column=1, row=1, padx=padx, pady=boxy, sticky=tk.EW)

        load_csv_button = ttk.Button(frame, text='Load from CSV', command=self.__on_load_csv)
        load_csv_button.grid(column=0, columnspan=2, row=2, padx=padx, sticky=tk.W)
        clear_button = ttk.Button(frame, text='Clear', command=self.__on_clear)
        clear_button.grid(column=2, row=2, padx=padx, sticky=tk.EW)

        self.__text_frame = tk.Text(frame)
        self.__text_frame.grid(column=0, columnspan=3, row=3, padx=padx, pady=pady, sticky=tk.NSEW)
        self.__text_frame.tag_configure('comment', foreground='grey')
        self.__set_route(self.systems)

        version = nb.Label(frame, text=f'Version: {_VERSION}', fg='grey')
        version.grid(column=0, columnspan=3, row=4, padx=padx, pady=pady, sticky=tk.E)
        return frame

    def __on_load_faction_systems(self):
        """ Load button for faction systems was pressed. """
        faction_name = self.__faction_name_var.get()
        min_age = self.__min_age_var.get()
        if not faction_name or min_age < 0:
            return
        _thread.start_new_thread(self.__load_faction_systems, (faction_name, min_age))

    def __load_faction_systems(self, faction_name, min_age):
        """ Load faction systems (in background). """
        try:
            route = []
            for faction in _ebgs_fetch_factions(faction_name):
                for presence in faction['faction_presence']:
                    sys_details = presence['system_details']
                    faction_presence = _FactionPresence(
                        presence['system_name'],
                        presence['updated_at'],
                        (sys_details['x'], sys_details['y'], sys_details['z']))
                    if faction_presence.age > timedelta(hours=min_age):
                        route.append(faction_presence)

            if not route:
                self.__text_frame.after_idle(
                    tk.messagebox.showinfo,
                    'Information',
                    f'No systems to update that are older than {min_age} hours.')
                return

            # Use first system if no start system is known
            start_system = self.start_system or route[0]

            # Optimise route for less jumps
            route = self.__optimise_route(start_system, route)

            # Update text field
            result = OrderedDict()
            previous_system = start_system
            for system in route:
                distance = system.distance_to(previous_system)
                previous_system = system
                result[system.name] = f'{system.nice_age}, {distance:.2f} Ly'
            self.__text_frame.after_idle(self.__set_route, result)

        except Exception as e:  # pylint: disable=broad-except
            _logger.exception('Error loading faction systems')
            self.__text_frame.after_idle(
                tk.messagebox.showerror,
                'Download error',
                f'Error downloading faction systems: {e}.')

    def __optimise_route(self, start_system, systems):
        """ Optimise list of systems for fewer jumps. """
        systems = list(systems)
        route = [start_system]
        while systems:
            # Find nearest
            cur_system = route[-1]
            closest_system = min(systems, key=cur_system.distance_to)
            # Remove system from list and add to route
            systems.remove(closest_system)
            route.append(closest_system)

        # Return route without start system
        return route[1:]

    def __on_load_csv(self):
        """ Load systems from CSV file. """
        filetypes = (('CSV files', '*.csv'), ('Text files', '*.txt'), ('All files', '*.*'))
        filename = tk.filedialog.askopenfilename(
            title='Open a file',
            initialdir='/',
            filetypes=filetypes)
        if not filename:
            return
        try:
            with open(filename, encoding='utf-8', newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                self.__text_frame.delete('1.0', tk.END)
                result = OrderedDict()
                for row in reader:
                    if not row:
                        continue
                    system, *rest = row.values()
                    result[system] = ', '.join(rest)
                self.__set_route(result)
        except Exception as e:  # pylint: disable=broad-except
            _logger.exception('Error loading CSV file')
            tk.messagebox.showerror('Download error', f'Error loading CSV file: {e}.')

    def __set_route(self, route):
        """ Write route to text field. """
        self.__text_frame.delete('1.0', tk.END)
        for system, comment in route.items():
            if system:
                self.__text_frame.insert(tk.END, system)
                self.__text_frame.insert(tk.END, ' ')
            if comment:
                self.__text_frame.insert(tk.END, '# ', 'comment')
                self.__text_frame.insert(tk.END, comment, 'comment')
            self.__text_frame.insert(tk.END, '\n')

    def __on_clear(self):
        """ Clear button was pressed. """
        self.__text_frame.delete('1.0', tk.END)

    def on_change(self):
        """ Preferences need to get applied. """
        _PluginConfigs.FACTION_NAME.set(self.__faction_name_var.get().strip())
        _PluginConfigs.MIN_AGE.set(self.__min_age_var.get())
        text = self.__text_frame.get('1.0', tk.END)
        self.systems.clear()
        for line in text.splitlines():
            values = line.split('#', 1)
            system = values.pop(0).strip()
            if system:
                self.systems[system] = values[0].strip() if values else ''


class _PluginApp:
    """ Plugin application. """

    def __init__(self):
        self.__systems = OrderedDict()
        self.__label = None

    def set_systems(self, systems):
        """ Set systems for route. """
        self.__systems = systems
        self.__update_next_system()

    def create_frame(self, parent: tk.Frame):
        """ Create and return application frames. """
        self.__label = tk.Label(parent, anchor="w")
        self.__label.bind("<Double-Button-1>", self.__on_skip)
        return (tk.Label(parent, text='Next system:'), self.__label)

    def on_journal_entry(self, system):
        """ New journal entry. """
        if self.__systems and system in self.__systems:
            del self.__systems[system]
            self.__update_next_system()

    def __on_skip(self, _event):
        """ Skip next system. """
        if self.__systems:
            self.__systems.popitem(last=False)
            self.__update_next_system()

    def __update_next_system(self):
        """ Update UI and clipboard. """
        next_system = list(self.__systems.keys())[0] if self.__systems else ''
        if next_system:
            self.__label['text'] = f'{next_system} ({len(self.__systems)})'
            self.__label.clipboard_clear()
            self.__label.clipboard_append(next_system)
            self.__label.update()
        else:
            self.__label['text'] = ''


_plugin_prefs = _PluginPrefs()
_plugin_app = _PluginApp()


def plugin_start3(_plugin_dir: str) -> str:
    """ Called by EDMC to start plugin. """
    return _plugin_name


def plugin_prefs(parent: nb.Notebook, _cmdr: str, _is_beta: bool) -> Optional[tk.Frame]:
    """ Called by EDMC when showing preferences. """
    return _plugin_prefs.create_frame(parent)


def prefs_changed(_cmdr: str, _is_beta: bool) -> None:
    """ Called by EDMC when preferences are applied. """
    _plugin_prefs.on_change()
    _plugin_app.set_systems(_plugin_prefs.systems)


def plugin_app(parent: tk.Frame) -> tk.Frame:
    """ Called by EDMC when the application is started. """
    return _plugin_app.create_frame(parent)


def journal_entry(
    _cmdr: str,
    _is_beta: bool,
    system: str,
    _station: str,
    entry: Dict[str, Any],
    _state: Dict[str, Any]
) -> Optional[str]:
    """ Called by EDMC for every new journal entry. """
    if system:
        if 'StarSystem' in entry and 'StarPos' in entry:
            timestamp = entry['timestamp']
            star_system = entry['StarSystem']
            location = entry['StarPos']
            _plugin_prefs.start_system = _FactionPresence(star_system, timestamp, location)
        _plugin_app.on_journal_entry(system)
