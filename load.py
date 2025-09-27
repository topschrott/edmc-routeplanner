""" EDMC plugin for copying next system in route to clipboard. """
import _thread
import logging
import os
import math
import tkinter as tk
from datetime import datetime, timezone, timedelta
from tkinter import ttk
from enum import Enum
from typing import Optional, Dict, Any

import requests

import myNotebook as nb
from config import config
from config import appname


_plugin_name = os.path.basename(os.path.dirname(__file__))
_logger = logging.getLogger(f'{appname}.{_plugin_name}')


def _ebgs_fetch(path, params):
    """ Fetch data and all pages from Elite BGS.

        Check out https://elitebgs.app/ebgs/docs/V5/
    """
    url = f'https://elitebgs.app/api/ebgs/v5/{path}'
    items = []
    while True:
        response = requests.get(url, params=params, timeout=60).json()
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

    def get_str(self):
        """ Return string setting value. """
        return config.get_str(self.value)

    def set(self, config_value):
        """ Set new value for setting. """
        config.set(self.value, config_value)


class _FactionPresence:

    def __init__(self, system_name, updated_at, location):
        self.system_name = system_name
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
        self.__text_frame = None
        self.systems = []
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
        frame.rowconfigure(2, weight=1)

        label = nb.Label(frame, text='Faction name:')
        label.grid(column=0, row=0, padx=padx, pady=pady, sticky=tk.W)

        entry = nb.Entry(frame, takefocus=False, textvariable=self.__faction_name_var)
        entry.grid(column=1, row=0, padx=padx, pady=boxy, sticky=tk.EW)

        load_button = ttk.Button(frame, text='Load', command=self.__on_load_faction_systems)
        load_button.grid(column=2, row=0, sticky=tk.EW)

        clear_button = ttk.Button(frame, text='Clear', command=self.__on_clear)
        clear_button.grid(column=2, row=1, sticky=tk.EW)

        self.__text_frame = tk.Text(frame)
        self.__text_frame.insert(tk.END, '\n'.join(self.systems))
        self.__text_frame.grid(column=0, columnspan=3, row=2, padx=padx, pady=pady, sticky=tk.NSEW)
        return frame

    def __on_load_faction_systems(self):
        """ Load button for faction systems was pressed. """
        faction_name = self.__faction_name_var.get()
        _thread.start_new_thread(self.__load_faction_systems, (faction_name,))

    def __load_faction_systems(self, faction_name):
        """ Load faction systems (in background). """
        try:
            faction_systems = []
            for faction in _ebgs_fetch_factions(faction_name):
                for presence in faction['faction_presence']:
                    sys_details = presence['system_details']
                    faction_systems.append(_FactionPresence(
                        presence['system_name'],
                        presence['updated_at'],
                        (sys_details['x'], sys_details['y'], sys_details['z'])))

            # Optimise route for less jumps
            start_system = self.start_system or faction_systems[0]
            faction_systems = self.__optimise_route(start_system, faction_systems)

            self.__text_frame.after_idle(self.__faction_systems_received, faction_systems)

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

    def __faction_systems_received(self, faction_systems):
        """ List of systems loaded. """
        # Filter up-to-date systems
        faction_systems = [s for s in faction_systems if s.age > timedelta(hours=2)]
        # Sort by update time
        faction_systems.sort(key=lambda x: x.updated_at)

        text = '\n'.join(
            f'{s.system_name} # {s.nice_age}' for s in faction_systems
        )
        self.__text_frame.delete('1.0', tk.END)
        self.__text_frame.insert(tk.END, text)

    def __on_clear(self):
        """ Clear button was pressed. """
        self.__text_frame.delete('1.0', tk.END)

    def on_change(self):
        """ Preferences need to get applied. """
        _PluginConfigs.FACTION_NAME.set(self.__faction_name_var.get())
        text = self.__text_frame.get('1.0', tk.END)
        self.systems = []
        for line in text.splitlines():
            line = line.split('#', 1)[0].strip()
            if line:
                self.systems.append(line)


class _PluginApp:
    """ Plugin application. """

    def __init__(self):
        self.__systems = []
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
            self.__systems.remove(system)
            self.__update_next_system()

    def __on_skip(self, _event):
        """ Skip next system. """
        if self.__systems:
            self.__systems.pop(0)
            self.__update_next_system()

    def __update_next_system(self):
        """ Update UI and clipboard. """
        next_system = self.__systems[0] if self.__systems else ''
        self.__label['text'] = next_system
        if next_system:
            self.__label.clipboard_clear()
            self.__label.clipboard_append(next_system)
            self.__label.update()


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
