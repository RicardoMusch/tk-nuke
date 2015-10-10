# Copyright (c) 2015 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.

"""
Menu handling for Nuke and Hiero.

"""

import tank
import sys
import nuke
import os
import unicodedata
import nukescripts.openurl
import nukescripts

from PySide import QtGui

# -----------------------------------------------------------------------------

class MenuGenerator(object):
    def __new__(cls, *args, **kwargs):
        if cls is MenuGenerator:
            if nuke.env.get("hiero"):
                return HieroMenuGenerator(*args, **kwargs)
            else:
                return NukeMenuGenerator(*args, **kwargs)
        else:
            return super(MenuGenerator, cls).__new__(cls, *args, **kwargs)

    def __init__(self, engine, menu_name):
        self._engine = engine
        self._menu_name = menu_name

    @property
    def engine(self):
        return self._engine

    @property
    def menu_name(self):
        return self._menu_name

# -----------------------------------------------------------------------------

class HieroMenuGenerator(MenuGenerator):
    def __init__(self, *args, **kwargs):
        super(HieroMenuGenerator, self).__init__(*args, **kwargs)
        self._menu_handle = None
        self._context_menus_to_apps = dict()

    def create_menu(self):
        """
        Create the Tank Menu
        """
        import hiero
        if self._menu_handle is not None:
            self.destroy_menu()

        self._menu_handle = QtGui.QMenu("Shotgun")
        help = hiero.ui.findMenuAction("Cache")
        menuBar = hiero.ui.menuBar()
        menuBar.insertMenu(help, self._menu_handle)

        self._menu_handle.clear()

        # now add the context item on top of the main menu
        self._context_menu = self._add_context_menu()
        self._menu_handle.addSeparator()

        # now enumerate all items and create menu objects for them
        menu_items = []
        for (cmd_name, cmd_details) in self.engine.commands.items():
            menu_items.append(HieroAppCommand(self.engine, cmd_name, cmd_details))

        # now add favourites
        for fav in self.engine.get_setting("menu_favourites"):
            app_instance_name = fav["app_instance"]
            menu_name = fav["name"]
            # scan through all menu items
            for cmd in menu_items:
                if cmd.app_instance_name == app_instance_name and cmd.name == menu_name:
                    # found our match!
                    cmd.add_command_to_menu(self._menu_handle)
                    # mark as a favourite item
                    cmd.favourite = True

        # get the apps for the various context menus
        self._context_menus_to_apps = {
            "bin_context_menu": [],
            "timeline_context_menu": [],
            "spreadsheet_context_menu": [],
        }

        remove = set()
        for (key, apps) in self._context_menus_to_apps.iteritems():
            items = self.engine.get_setting(key)
            for item in items:
                app_instance_name = item["app_instance"]
                menu_name = item["name"]
                # scan through all menu items
                for (i, cmd) in enumerate(menu_items):
                    if cmd.app_instance_name == app_instance_name and cmd.name == menu_name:
                        # found th match
                        apps.append(cmd)
                        cmd.requires_selection = item["requires_selection"]
                        if not item["keep_in_menu"]:
                            remove.add(i)
                        break

        for index in sorted(remove, reverse=True):
            del menu_items[index]

        # register for the interesting events
        hiero.core.events.registerInterest(
            "kShowContextMenu/kBin",
            self.eventHandler,
        )
        hiero.core.events.registerInterest(
            "kShowContextMenu/kTimeline",
            self.eventHandler,
        )
        # note that the kViewer works differently than the other things
        # (returns a hiero.ui.Viewer object: http://docs.thefoundry.co.uk/hiero/10/hieropythondevguide/api/api_ui.html#hiero.ui.Viewer)
        # so we cannot support this easily using the same principles as for the other things....
        hiero.core.events.registerInterest(
            "kShowContextMenu/kSpreadsheet",
            self.eventHandler,
        )
        self._menu_handle.addSeparator()

        # now go through all of the menu items.
        # separate them out into various sections
        commands_by_app = {}

        for cmd in menu_items:
            if cmd.type == "context_menu":
                # context menu!
                cmd.add_command_to_menu(self._context_menu)
            else:
                # normal menu
                app_name = cmd.app_name
                if app_name is None:
                    # un-parented app
                    app_name = "Other Items"
                if not app_name in commands_by_app:
                    commands_by_app[app_name] = []
                commands_by_app[app_name].append(cmd)

        # now add all apps to main menu
        self._add_app_menu(commands_by_app)

    def destroy_menu(self):
        import hiero
        menuBar = hiero.ui.menuBar()
        menuBar.removeAction(self._menu_handle.menuAction())
        self._menu_handle = None

    def eventHandler(self, event):
        if event.subtype == "kBin":
            cmds = self._context_menus_to_apps["bin_context_menu"]
        elif event.subtype == "kTimeline":
            cmds = self._context_menus_to_apps["timeline_context_menu"]
        elif event.subtype == "kSpreadsheet":
            cmds = self._context_menus_to_apps["spreadsheet_context_menu"]

        if not cmds:
            return

        event.menu.addSeparator()
        menu = event.menu.addAction("Shotgun")
        menu.setEnabled(False)

        for cmd in cmds:
            enabled = True
            if cmd.requires_selection:
                if hasattr(event.sender, "selection") and not event.sender.selection():
                    enabled = False
            cmd.sender = event.sender
            cmd.event_type = event.type
            cmd.event_subtype = event.subtype
            cmd.add_command_to_menu(event.menu, enabled)
        event.menu.addSeparator()

    def _add_context_menu(self):
        """
        Adds a context menu which displays the current context
        """
        ctx = self.engine.context

        if ctx.entity is None:
            ctx_name = "%s" % ctx.project["name"]
        elif ctx.step is None and ctx.task is None:
            # entity only
            # e.g. Shot ABC_123
            ctx_name = "%s %s" % (ctx.entity["type"], ctx.entity["name"])
        else:
            # we have either step or task
            task_step = None
            if ctx.step:
                task_step = ctx.step.get("name")
            if ctx.task:
                task_step = ctx.task.get("name")

            # e.g. [Lighting, Shot ABC_123]
            ctx_name = "%s, %s %s" % (task_step, ctx.entity["type"], ctx.entity["name"])

        # create the menu object
        ctx_menu = self._menu_handle.addMenu(ctx_name)
        action = ctx_menu.addAction("Jump to Shotgun")
        action.triggered.connect(self._jump_to_sg)
        action = ctx_menu.addAction("Jump to File System")
        action.triggered.connect(self._jump_to_fs)
        ctx_menu.addSeparator()

        return ctx_menu

    def _jump_to_sg(self):
        """
        Jump from context to Sg
        """
        from tank.platform.qt import QtCore, QtGui
        url = self.engine.context.shotgun_url
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _jump_to_fs(self):
        """
        Jump from context to Fs
        """
        paths = self.engine.context.filesystem_locations
        for disk_location in paths:
            # get the setting
            system = sys.platform

            # run the app
            if system == "linux2":
                cmd = 'xdg-open "%s"' % disk_location
            elif system == "darwin":
                cmd = 'open "%s"' % disk_location
            elif system == "win32":
                cmd = 'cmd.exe /C start "Folder" "%s"' % disk_location
            else:
                raise Exception("Platform '%s' is not supported." % system)

            exit_code = os.system(cmd)
            if exit_code != 0:
                self.engine.log_error("Failed to launch '%s'!" % cmd)

    def _add_app_menu(self, commands_by_app):
        """
        Add all apps to the main menu, process them one by one.
        """
        for app_name in sorted(commands_by_app.keys()):
            if len(commands_by_app[app_name]) > 1:
                # more than one menu entry fort his app
                # make a sub menu and put all items in the sub menu
                app_menu = self._menu_handle.addMenu(app_name)
                for cmd in commands_by_app[app_name]:
                    cmd.add_command_to_menu(app_menu)
            else:
                # this app only has a single entry.
                # display that on the menu
                # todo: Should this be labeled with the name of the app
                # or the name of the menu item? Not sure.
                cmd_obj = commands_by_app[app_name][0]
                if not cmd_obj.favourite:
                    # skip favourites since they are already on the menu
                    cmd_obj.add_command_to_menu(self._menu_handle)

# -----------------------------------------------------------------------------

class NukeMenuGenerator(MenuGenerator):
    """
    Menu generation functionality for Nuke
    """

    def __init__(self, *args, **kwargs):
        super(NukeMenuGenerator, self).__init__(*args, **kwargs)
        self._dialogs = []
        engine_root_dir = self.engine.disk_location
        self._shotgun_logo = os.path.abspath(
            os.path.join(
                engine_root_dir,
                "resources",
                "sg_logo_80px.png",
            ),
        )
        self._shotgun_logo_blue = os.path.abspath(
            os.path.join(
                engine_root_dir,
                "resources",
                "sg_logo_blue_32px.png",
            ),
        )

    def create_menu(self):
        """
        Render the entire Shotgun menu.
        """
        # create main Shotgun menu
        menu_handle = nuke.menu("Nuke").addMenu(self._menu_name)

        # create tank side menu
        node_menu_handle = nuke.menu("Nodes").addMenu(self._menu_name, icon=self._shotgun_logo)

        # slight hack here but first ensure that menus are empty
        # this is to ensure we can recover from weird context switches
        # where the engine didn't clean up after itself properly
        menu_handle.clearMenu()
        node_menu_handle.clearMenu()

        # now add the context item on top of the main menu
        self._context_menu = self._add_context_menu(menu_handle)
        menu_handle.addSeparator()

        # now enumerate all items and create menu objects for them
        menu_items = []
        for (cmd_name, cmd_details) in self.engine.commands.items():
             menu_items.append(NukeAppCommand(self.engine, cmd_name, cmd_details))

        # sort list of commands in name order
        menu_items.sort(key=lambda x: x.name)

        # now add favourites
        for fav in self.engine.get_setting("menu_favourites"):
            app_instance_name = fav["app_instance"]
            menu_name = fav["name"]

            # scan through all menu items
            for cmd in menu_items:
                 if cmd.app_instance_name == app_instance_name and cmd.name == menu_name:
                     # found our match!
                     cmd.add_command_to_menu(menu_handle)
                     # mark as a favourite item
                     cmd.favourite = True
        menu_handle.addSeparator()
        
        # now go through all of the menu items.
        # separate them out into various sections
        commands_by_app = {}
        
        for cmd in menu_items:
            if cmd.type == "node":
                # add to the node menu
                # get icon if specified - default to tank icon if not specified
                icon = cmd.properties.get("icon", self._shotgun_logo)
                node_menu_handle.addCommand(cmd.name, cmd.callback, icon=icon)
            elif cmd.type == "context_menu":
                # context menu!
                cmd.add_command_to_menu(self._context_menu)
            else:
                # normal menu
                app_name = cmd.app_name
                if app_name is None:
                    # un-parented app
                    app_name = "Other Items" 
                if not app_name in commands_by_app:
                    commands_by_app[app_name] = []
                commands_by_app[app_name].append(cmd)

            # in addition to being added to the normal menu above,
            # panel menu items are also added to the pane menu
            if cmd.type == "panel":
                # first make sure the Shotgun pane menu exists
                pane_menu = nuke.menu("Pane").addMenu("Shotgun", icon=self._shotgun_logo)
                # now set up the callback
                cmd.add_command_to_pane_menu(pane_menu)
        
        # now add all apps to main menu
        self._add_app_menu(commands_by_app, menu_handle)

    def destroy_menu(self):
        # important!
        # the menu code in nuke seems quite unstable, so make sure to test 
        # any changes done in relation to menu deletion carefully.
        # the removeItem() method seems to work on some version of Nuke, but not all.
        # for example the following code works in nuke 7, not nuke 6:
        # nuke.menu("Nuke").removeItem("Shotgun")
        
        # the strategy below is to be as safe as possible, acquire a handle to 
        # the menu by iteration (if you store the handle object, they may expire
        # and when you try to access them they underlying object is gone and things 
        # will crash). clearMenu() seems to work on both v6 and v7.
        menus = ["Nuke", "Pane", "Nodes"]
        for menu in menus:
            # find the menu and iterate over all items
            for mh in nuke.menu(menu).items():
                # look for the shotgun menu
                if mh.name() == self._menu_name:
                    # and clear it
                    mh.clearMenu()

    def _add_context_menu(self, menu_handle):
        """
        Adds a context menu which displays the current context
        """        
        ctx = self.engine.context
        ctx_name = str(ctx)

        # create the menu object        
        ctx_menu = menu_handle.addMenu(ctx_name, icon=self._shotgun_logo_blue)
        ctx_menu.addCommand("Jump to Shotgun", self._jump_to_sg)
        ctx_menu.addCommand("Jump to File System", self._jump_to_fs)
        ctx_menu.addSeparator()
        return ctx_menu

    def _jump_to_sg(self):
        """
        Jump to shotgun, launch web browser
        """
        from tank.platform.qt import QtCore, QtGui        
        url = self.engine.context.shotgun_url
        nukescripts.openurl.start(url)        

    def _jump_to_fs(self):
        
        """
        Jump from context to FS
        """
        # launch one window for each location on disk
        paths = self.engine.context.filesystem_locations
        for disk_location in paths:
            # get the setting        
            system = sys.platform
            # run the app
            if system == "linux2":
                cmd = 'xdg-open "%s"' % disk_location
            elif system == "darwin":
                cmd = 'open "%s"' % disk_location
            elif system == "win32":
                cmd = 'cmd.exe /C start "Folder" "%s"' % disk_location
            else:
                raise Exception("Platform '%s' is not supported." % system)
            
            exit_code = os.system(cmd)
            if exit_code != 0:
                self.engine.log_error("Failed to launch '%s'!" % cmd)

    def _add_app_menu(self, commands_by_app, menu_handle):
        """
        Add all apps to the main menu, process them one by one.
        """
        for app_name in sorted(commands_by_app.keys()):
            if len(commands_by_app[app_name]) > 1:
                # more than one menu entry fort his app
                # make a sub menu and put all items in the sub menu
                app_menu = menu_handle.addMenu(app_name)
                
                # get the list of menu cmds for this app
                cmds = commands_by_app[app_name]
                # make sure it is in alphabetical order
                cmds.sort(key=lambda x: x.name) 
                
                for cmd in cmds:
                    cmd.add_command_to_menu(app_menu)
            else:
                # this app only has a single entry. 
                # display that on the menu
                # todo: Should this be labelled with the name of the app 
                # or the name of the menu item? Not sure.
                cmd_obj = commands_by_app[app_name][0]
                if not cmd_obj.favourite:
                    # skip favourites since they are alreay on the menu
                    cmd_obj.add_command_to_menu(menu_handle)

# -----------------------------------------------------------------------------

class AppCommand(object):
    def __init__(self, engine, name, command_dict):
        self._name = name
        self._engine = engine
        self._properties = command_dict["properties"]
        self._callback = command_dict["callback"]
        self._favourite = False
        self._app = self._properties.get("app")
        self._type = self._properties.get("type", "default")

        try:
            self._app_name = self._app.display_name
        except AttributeError:
            self._app_name = None

        self._app_instance_name = None
        if self._app:
            for (app_instance_name, app_instance_obj) in engine.apps.items():
                if self._app and self._app == app_instance_obj:
                    self._app_instance_name = app_instance_name

    @property
    def app(self):
        return self._app

    @property
    def app_instance_name(self):
        return self._app_instance_name

    @property
    def app_name(self):
        return self._app_name

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        self._name = str(name)

    @property
    def engine(self):
        return self._engine

    @property
    def properties(self):
        return self._properties

    @property
    def callback(self):
        return self._callback

    @property
    def favourite(self):
        return self._favourite

    @favourite.setter
    def favourite(self, state):
        self._favourite = bool(state)

    @property
    def type(self):
        return self._type

    def add_command_to_menu(self, menu, enabled=True):
        raise NotImplementedError()

    def add_command_to_pane_menu(self, menu):
        raise NotImplementedError()

    def get_documentation_url_str(self):
        """
        Returns the documentation as a str.
        """
        if self.app:
            doc_url = self.app.documentation_url
            # Deal with nuke's inability to handle unicode.
            if doc_url.__class__ == unicode:
                doc_url = unicodedata.normalize("NFKD", doc_url).encode("ascii", "ignore")
            return doc_url
        return None

# -----------------------------------------------------------------------------

class HieroAppCommand(AppCommand):
    """
    Wraps around a single command that you get from engine.commands
    """
    def __init__(self, engine, name, command_dict):
        super(HieroAppCommand, self).__init__(engine, name, command_dict)
        self._requires_selection = False
        self._sender = None
        self._event_type = None
        self._event_subtype = None

    @property
    def requires_selection(self):
        return self._requires_selection

    @requires_selection.setter
    def requires_selection(self, state):
        self._requires_selection = bool(state)

    @property
    def sender(self):
        return self._sender

    @sender.setter
    def sender(self, sender):
        self._sender = sender

    @property
    def event_type(self):
        return self._event_type

    @event_type.setter
    def event_type(self, event_type):
        self._event_type = event_type

    @property
    def event_subtype(self):
        return self._event_subtype

    @event_subtype.setter
    def event_subtype(self, event_subtype):
        self._event_subtype = event_subtype

    def add_command_to_menu(self, menu, enabled=True):
        """
        Adds an app command to the menu
        """
        icon = self.properties.get("icon")
        action = menu.addAction(self.name)
        action.setEnabled(enabled)
        if icon:
            action.setIcon(QtGui.QIcon(icon))

        def handler():
            # populate special action context
            # this is read by apps and hooks 
            
            # in hiero, sender parameter for hiero.core.events.EventType.kShowContextMenu
            # is supposed to always of class binview:
            #
            # http://docs.thefoundry.co.uk/hiero/10/hieropythondevguide/api/api_ui.html?highlight=sender#hiero.ui.BinView
            #
            # In reality, however, it seems it returns the following items:
            # ui.Hiero.Python.TimelineEditor object at 0x11ab15248
            # ui.Hiero.Python.SpreadsheetView object at 0x11ab152d8>
            # ui.Hiero.Python.BinView
            #
            # These objects all have a selection property that returns a list of objects.
            # We extract the selected objects and set the engine "last clicked" state:
            
            # set the engine last clicked selection state
            if self.sender:
                self.engine._last_clicked_selection = self.sender.selection()
            else:
                # main menu
                self.engine._last_clicked_selection = []
            
            # set the engine last clicked selection area
            if self.event_type == "kBin":
                self.engine._last_clicked_area = self.engine.HIERO_BIN_AREA
            elif self.event_type == "kTimeline":
                self.engine._last_clicked_area = self.engine.HIERO_TIMELINE_AREA
            elif self.event_type == "kSpreadsheet":
                self.engine._last_clicked_area = self.engine.HIERO_SPREADSHEET_AREA
            else:
                self.engine._last_clicked_area = None
            
            self.engine.log_debug("")
            self.engine.log_debug("--------------------------------------------")
            self.engine.log_debug("A menu item was clicked!")
            self.engine.log_debug("Event Type: %s / %s" % (self.event_type, self.event_subtype))
            self.engine.log_debug("Selected Objects:")

            for x in self.engine._last_clicked_selection:
                self.engine.log_debug("- %r" % x)
            self.engine.log_debug("--------------------------------------------")
            
            # and fire the callback
            self.callback()
        action.triggered.connect(handler)

# -----------------------------------------------------------------------------

class NukeAppCommand(AppCommand):
    """
    Wraps around a single command that you get from engine.commands
    """   
    def _non_pane_menu_callback_wrapper(self, callback):
        """
        Callback for all non pane menu commands
        """
        # this is a wrapped menu callback for whenever an item is clicked
        # in a menu which isn't the standard nuke pane menu. This ie because 
        # the standard pane menu in nuke provides nuke with an implicit state
        # so that nuke knows where to put the panel when it is created.
        # if the command is called from a non-pane menu however, this implicity
        # state does not exist and needs to be explicity defined.
        #
        # for this purpose, we set a global flag to hint to the panelling 
        # logic to run its special window logic in this case.
        #
        # note that because of nuke not using the import_module()
        # system, it's hard to obtain a reference to the engine object
        # right here - this is why we set a flag on the main tank
        # object like this.
        setattr(tank, "_callback_from_non_pane_menu", True)
        try:
            callback()
        finally:    
            delattr(tank, "_callback_from_non_pane_menu")
        
    def add_command_to_pane_menu(self, menu):
        """
        Add a command to the pane menu
        
        :param menu: Menu object to add the new item to
        """
        icon = self.properties.get("icon")
        menu.addCommand(self.name, self.callback, icon=icon)
        
    def add_command_to_menu(self, menu, enabled=True):
        """
        Adds an app command to the menu
        
        :param menu: Menu object to add the new item to
        """
        # std shotgun menu
        icon = self.properties.get("icon")
        hotkey = self.properties.get("hotkey")
        
        # now wrap the command callback in a wrapper (see above)
        # which sets a global state variable. This is detected
        # by the show_panel so that it can correctly establish 
        # the flow for when a pane menu is clicked and you want
        # the potential new panel to open in that window.
        cb = lambda: self._non_pane_menu_callback_wrapper(self.callback)

        if hotkey:
            menu.addCommand(self.name, cb, hotkey, icon=icon)
        else:
            menu.addCommand(self.name, cb, icon=icon)

# -----------------------------------------------------------------------------

