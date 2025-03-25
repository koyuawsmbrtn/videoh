# window.py
#
# Copyright 2025 koyu.space
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import json
import requests
import subprocess
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk, Gio, GLib, GdkPixbuf, Pango, Gdk
from gettext import gettext as _
from videoh.imdb import IMDb
from videoh.tvmaze import TVMaze
from .item import VideohItem
from .player import VideohPlayer
from .episodes import EpisodesUI
import re
import threading

@Gtk.Template(resource_path='/space/koyu/videoh/settings.ui')
class VideohPreferencesWindow(Adw.PreferencesWindow):
    __gtype_name__ = 'VideohPreferencesWindow'
    
    imdb_switch = Gtk.Template.Child()
    tvmaze_switch = Gtk.Template.Child()
    mal_switch = Gtk.Template.Child()
    auto_fetch_switch = Gtk.Template.Child()
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = Gio.Settings.new('space.koyu.videoh')
        
        self.imdb_switch.set_active(self.settings.get_boolean('use-imdb'))
        self.tvmaze_switch.set_active(self.settings.get_boolean('use-tvmaze'))  # Update setting name
        self.mal_switch.set_active(self.settings.get_boolean('use-mal'))
        self.auto_fetch_switch.set_active(self.settings.get_boolean('auto-fetch'))
        
        self.imdb_switch.connect('notify::active', self.on_imdb_switch_active)
        self.tvmaze_switch.connect('notify::active', self.on_tvmaze_switch_active)  # Update method name
        self.mal_switch.connect('notify::active', self.on_mal_switch_active)
        self.auto_fetch_switch.connect('notify::active', self.on_auto_fetch_switch_active)
    
    def on_imdb_switch_active(self, switch, _):
        self.settings.set_boolean('use-imdb', switch.get_active())
    
    def on_tvmaze_switch_active(self, switch, _):  # Rename method
        self.settings.set_boolean('use-tvmaze', switch.get_active())
    
    def on_mal_switch_active(self, switch, _):
        self.settings.set_boolean('use-mal', switch.get_active())
    
    def on_auto_fetch_switch_active(self, switch, _):
        self.settings.set_boolean('auto-fetch', switch.get_active())

@Gtk.Template(resource_path='/space/koyu/videoh/window.ui')
class VideohWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'VideohWindow'

    navigation_view = Gtk.Template.Child()
    view_stack = Gtk.Template.Child()
    movies_box = Gtk.Template.Child()
    shows_box = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()
    
    # Add template children for search
    search_bar = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    search_mode = Gtk.Template.Child()
    show_search_btn = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = Gio.Settings.new('space.koyu.videoh')
        self.config_dir = Path(GLib.get_user_config_dir()) / "videoh"
        self.cache_dir = Path(GLib.get_user_cache_dir()) / "videoh"
        self.videos_dir = Path.home() / "Videos"
        self.metadata_file = self.config_dir / "metadata.json"
        self.setup_directories()
        self.setup_actions()
        self.load_library()
        self.populate_ui()
        
        # Load CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_path(str(Path(__file__).parent / "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Check for auto-fetch setting
        if self.settings.get_boolean('auto-fetch'):
            GLib.idle_add(self.on_fetch_metadata, None, None)
        
        # Connect refresh button
        self.refresh_button.connect('clicked', lambda _: self.on_fetch_metadata(None, None))

        # Connect search signals
        self.show_search_btn.connect('toggled', self.on_show_search_toggled)
        self.search_entry.connect('search-changed', self.on_search_changed)
        self.search_mode.connect('notify::selected', self.on_search_changed)

    def setup_actions(self):
        """Set up window actions"""
        actions = [
            ('fetch-metadata', self.on_fetch_metadata),
            ('settings', self.on_settings),
            ('help', self.on_help),
            ('about', self.on_about),
            ('open-folder', self.on_open_folder),
            ('view-sorting', self.on_view_sorting, 's')
        ]
        
        for action_def in actions:
            if len(action_def) == 2:
                name, callback = action_def
                action = Gio.SimpleAction.new(name, None)
                action.connect('activate', callback)
            else:
                name, callback, param_type = action_def
                action = Gio.SimpleAction.new(name, GLib.VariantType(param_type))
                action.connect('activate', callback)
            
            self.add_action(action)  # Add to window instead of application

    def setup_directories(self):
        # Create necessary directories if they don't exist
        for directory in [self.config_dir, self.cache_dir, 
                         self.videos_dir / "Movies", 
                         self.videos_dir / "Shows"]:
            directory.mkdir(parents=True, exist_ok=True)
        
        # Create metadata file if it doesn't exist
        if not self.metadata_file.exists():
            self.metadata_file.write_text("{}")

    def load_library(self):
        # Load existing metadata
        try:
            with open(self.metadata_file, 'r') as f:
                self.metadata = json.load(f)
        except json.JSONDecodeError:
            self.metadata = {}

        # Scan Movies directory
        movies_path = self.videos_dir / "Movies"
        self.movies = []
        for movie_file in movies_path.glob("**/*"):
            if movie_file.is_file() and movie_file.suffix.lower() in ['.mp4', '.mkv', '.avi', '.webm']:
                movie_data = {
                    'path': str(movie_file),
                    'title': movie_file.stem,
                    'metadata': self.metadata.get(str(movie_file), {})
                }
                self.movies.append(movie_data)

        # Scan Shows directory with seasons support
        shows_path = self.videos_dir / "Shows"
        self.shows = {}
        for show_dir in shows_path.iterdir():
            if show_dir.is_dir():
                seasons = {}
                # Look for season directories or episodes
                for item in show_dir.iterdir():
                    if item.is_dir() and item.name.lower().startswith("season"):
                        # This is a season directory
                        season_num = item.name.lower().replace("season", "").strip()
                        episodes = []
                        for episode_file in item.glob("*"):
                            if episode_file.is_file() and episode_file.suffix.lower() in ['.mp4', '.mkv', '.avi', '.webm']:
                                episode_data = {
                                    'path': str(episode_file),
                                    'title': episode_file.stem,
                                    'season': season_num,
                                    'metadata': self.metadata.get(str(episode_file), {})
                                }
                                episodes.append(episode_data)
                        if episodes:
                            seasons[season_num] = episodes
                    elif item.is_file() and item.suffix.lower() in ['.mp4', '.mkv', '.avi', '.webm']:
                        # Episode directly in show directory - assume season 1
                        if "1" not in seasons:
                            seasons["1"] = []
                        episode_data = {
                            'path': str(item),
                            'title': item.stem,
                            'season': "1",
                            'metadata': self.metadata.get(str(item), {})
                        }
                        seasons["1"].append(episode_data)
                
                if seasons:
                    self.shows[show_dir.name] = seasons

    def save_metadata(self):
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2)

    def update_metadata(self, file_path, metadata):
        self.metadata[file_path] = metadata
        self.save_metadata()
        # Reload library and update UI
        self.load_library()
        self.populate_ui()

    def download_poster(self, url, movie_title):
        """Download and cache a poster image"""
        if not url:
            return None
            
        # Create a safe filename from the movie title
        safe_title = "".join(c for c in movie_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        poster_filename = f"{safe_title}.jpg"
        poster_path = self.cache_dir / "posters" / poster_filename
        
        # Create posters directory if it doesn't exist
        (self.cache_dir / "posters").mkdir(exist_ok=True)
        
        # Download and save the poster if it doesn't exist
        if not poster_path.exists():
            try:
                response = requests.get(url, stream=True)
                response.raise_for_status()
                
                with open(poster_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        
                return str(poster_path)
            except Exception as e:
                print(f"Error downloading poster for {movie_title}: {e}")
                return None
        
        return str(poster_path)

    def download_person_image(self, url, person_name, role):
        if not url:
            return None
        safe_name = "".join(c for c in person_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        image_filename = f"{safe_name}.jpg"
        image_path = self.cache_dir / role / image_filename
        
        # Create subdirectory for the role (e.g. "directors" or "cast")
        (self.cache_dir / role).mkdir(exist_ok=True)
        
        if not image_path.exists():
            try:
                response = requests.get(url, stream=True)
                response.raise_for_status()
                with open(image_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return str(image_path)
            except Exception as e:
                print(f"Error downloading {role} image for {person_name}: {e}")
                return None
        return str(image_path)

    def _fetch_movie_metadata(self, imdb, movie, progress_dialog):
        """Fetch metadata for a single movie"""
        try:
            # Search for movie
            search_results = imdb.search_movie(movie['title'])
            if search_results:
                # Get first matching result - handle both object and dict formats
                first_result = search_results[0]
                movie_id = first_result.getID() if hasattr(first_result, 'getID') else first_result.get('movieID')
                
                if movie_id:
                    movie_data = imdb.get_movie(movie_id)
                    
                    # Build metadata
                    metadata = {
                        'title': movie_data.get('title'),
                        'year': movie_data.get('year'),
                        'rating': movie_data.get('rating'),
                        'plot': movie_data.get('plot outline', ''),
                        'director': [p['name'] for p in movie_data.get('director', [])],
                        'cast': [p['name'] for p in movie_data.get('cast', [])[:5]],
                        'genres': movie_data.get('genres', []),
                        'type': 'movie'
                    }
                    
                    # Download poster if available
                    if movie_data.get('full-size cover url'):
                        poster_path = self.download_poster(
                            movie_data['full-size cover url'],
                            movie['title']
                        )
                        if poster_path:
                            metadata['poster'] = poster_path
                    
                    # Update metadata
                    self.update_metadata(movie['path'], metadata)
                    
        except Exception as e:
            print(f"Error processing movie {movie['title']}: {e}")

    def _fetch_show_metadata(self, tvmaze, show_name, seasons, progress_dialog):
        """Fetch metadata for a single TV show and its episodes"""
        try:
            # Search for show
            search_results = tvmaze.search_shows(show_name)
            if search_results:
                show_data = search_results[0]['show']
                
                # Build show metadata
                show_metadata = {
                    'title': show_data.get('name'),
                    'year': show_data.get('premiered', '').split('-')[0],
                    'rating': show_data.get('rating', {}).get('average'),
                    'plot': show_data.get('summary', ''),
                    'genres': show_data.get('genres', []),
                    'type': 'show',
                    'status': show_data.get('status'),
                    'network': show_data.get('network', {}).get('name')
                }
                
                # Download show poster
                if show_data.get('image', {}).get('original'):
                    poster_path = self.download_poster(
                        show_data['image']['original'],
                        show_name
                    )
                    if poster_path:
                        show_metadata['poster'] = poster_path
                
                # Store show metadata
                show_key = f"show:{show_name}"
                self.metadata[show_key] = show_metadata
                
                # Get episodes data
                episodes_data = tvmaze.get_show_episodes(show_data['id'])
                
                # Process each local episode
                for season_num, episodes in seasons.items():
                    for episode in episodes:
                        episode_number = self._get_episode_number(episode['title'])
                        if episode_number:
                            # Find matching episode in TVMaze data
                            tvmaze_episode = next(
                                (e for e in episodes_data 
                                 if e['season'] == int(season_num) 
                                 and e['number'] == episode_number),
                                None
                            )
                            
                            if tvmaze_episode:
                                episode_metadata = {
                                    'title': tvmaze_episode.get('name'),
                                    'plot': tvmaze_episode.get('summary', ''),
                                    'air_date': tvmaze_episode.get('airdate'),
                                    'season': season_num,
                                    'episode': episode_number,
                                    'is_episode': True,
                                    'show_name': show_name
                                }
                                
                                # Update episode metadata
                                self.update_metadata(episode['path'], episode_metadata)
                                
        except Exception as e:
            print(f"Error processing show {show_name}: {e}")

    def _finish_metadata_refresh(self):
        """Complete the metadata refresh by updating UI"""
        try:
            # Load updated data
            self.load_library()
            
            # Force layout update by switching views
            while self.navigation_view.get_visible_page() and \
                  self.navigation_view.get_visible_page().get_tag() != "main":
                self.navigation_view.pop()
            self.navigation_view.pop_to_tag("main")
            self.populate_ui()
            
            # Show success toast
            toast = Adw.Toast.new(_("Successfully fetched metadata"))
            toast.set_timeout(3)
            self.toast_overlay.add_toast(toast)
            
            # Force a redraw
            self.queue_draw()
            
        except Exception as e:
            print(f"Error refreshing UI: {e}")
        return False

    def _update_progress_safely(self, progress_dialog, text):
        """Update progress dialog text safely from any thread"""
        if not progress_dialog or not progress_dialog.get_visible():
            return
        GLib.idle_add(lambda: progress_dialog.set_body(text) if progress_dialog.get_visible() else None)

    def on_fetch_metadata(self, action, param):
        settings = Gio.Settings.new('space.koyu.videoh')
        
        # Create progress dialog
        progress_dialog = Adw.MessageDialog.new(
            self,
            _("Fetching Metadata"),
            _("Please wait while fetching metadata...")
        )
        
        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_size_request(32, 32)
        spinner.set_margin_top(12)
        spinner.set_margin_bottom(12)
        progress_dialog.set_extra_child(spinner)
        progress_dialog.add_response("cancel", _("Cancel"))
        progress_dialog.present()
        
        def fetch_metadata_async():
            try:
                # Process movies
                if settings.get_boolean('use-imdb'):
                    imdb = self.get_imdb()
                    if imdb:
                        for movie in self.movies:
                            if not progress_dialog.get_visible():
                                return
                            self._update_progress_safely(progress_dialog, _("Processing movie: {}").format(movie['title']))
                            self._fetch_movie_metadata(imdb, movie, progress_dialog)

                # Process TV shows
                if settings.get_boolean('use-tvmaze'):
                    tvmaze = self.get_tvmaze()
                    if tvmaze:
                        for show_name, seasons in self.shows.items():
                            if not progress_dialog.get_visible():
                                return
                            self._update_progress_safely(progress_dialog, _("Processing show: {}").format(show_name))
                            self._fetch_show_metadata(tvmaze, show_name, seasons, progress_dialog)

                # Save all metadata
                self.save_metadata()
                
                # Update UI on main thread
                GLib.idle_add(self._finish_metadata_refresh)
                GLib.idle_add(progress_dialog.close)
                
            except Exception as e:
                GLib.idle_add(self._show_error_dialog, str(e))
            finally:
                GLib.idle_add(progress_dialog.close)

        # Handle dialog response
        progress_dialog.connect("response", lambda d, r: d.close() if r == "cancel" else None)
        
        # Start background thread
        thread = threading.Thread(target=fetch_metadata_async)
        thread.daemon = True
        thread.start()

    def on_settings(self, action, param):
        settings = VideohPreferencesWindow(transient_for=self)
        settings.present()

    def on_about(self, action, param):
        about = Adw.AboutWindow(
            transient_for=self,
            application_name='Vide OH!',
            application_icon='space.koyu.videoh',
            developer_name='koyu.space',
            version='1.0',
            developers=['Leonie'],
            copyright='© 2025 koyu.space'
        )
        about.present()

    def on_help(self, action, param):
        dialog = Adw.MessageDialog.new(
            self,
            _("Folder Structure"),
            _("Videoh looks for your videos in the following locations:\n\n"
              "~/Videos/Movies/\n"
              "    Place your movie files here\n\n"
              "~/Videos/Shows/\n"
              "    ShowName/\n"
              "        Season 1/\n"
              "            episode1.mp4\n"
              "            episode2.mkv\n"
              "        Season 2/\n"
              "            episode1.mp4\n\n"
              "Files are supported in MP4, MKV, WEBM and AVI formats.")
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def on_open_folder(self, action, param):
        subprocess.run(['xdg-open', str(self.videos_dir)])

    def populate_ui(self):
        # Clear existing content from FlowBoxes
        self.movies_box.remove_all()
        self.shows_box.remove_all()
        
        # Common function to create poster card
        def create_poster_card(title, metadata, on_click, is_show=False):
            overlay = Gtk.Overlay()
            overlay.add_css_class('poster-box')
            
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.add_css_class('card')
            
            # Add poster image
            poster = metadata.get('poster')
            if poster and Path(poster).exists():
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    poster, 200, 300, False)
                image = Gtk.Picture.new_for_pixbuf(pixbuf)
            else:
                # Use different fallback icons for shows and movies
                icon_name = "video-television" if is_show else "image-missing"
                image = Gtk.Image.new_from_icon_name(icon_name)
                image.set_pixel_size(200)
            
            image.add_css_class('poster-image')
            box.append(image)
            
            # Add title label
            label = Gtk.Label(label=title)
            label.set_wrap(True)
            label.set_max_width_chars(20)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.add_css_class('heading')
            label.add_css_class('poster-label')
            box.append(label)
            
            # Add info button overlay
            info_button = Gtk.Button()
            info_button.set_icon_name('info-outline-symbolic' if not is_show else 'view-list-symbolic')
            info_button.add_css_class('circular')
            info_button.add_css_class('osd')
            info_button.set_valign(Gtk.Align.START)
            info_button.set_halign(Gtk.Align.END)
            info_button.set_margin_top(6)
            info_button.set_margin_end(6)
            
            # Connect info button click
            info_button.connect('clicked', on_click)
            
            # Add main box and button to overlay
            overlay.set_child(box)
            overlay.add_overlay(info_button)
            
            # Make box clickable
            click = Gtk.GestureClick.new()
            click.connect('pressed', lambda g, n, x, y: on_click(None))
            box.add_controller(click)
            
            return overlay
        
        # Add movies
        for movie in self.movies:
            title = movie.get('metadata', {}).get('title', movie['title'])
            card = create_poster_card(
                title,
                movie.get('metadata', {}),
                lambda _, m=movie: self.show_movie_details(m)
            )
            self.movies_box.append(card)
        
        # Add TV shows
        for show_name, seasons in self.shows.items():
            # Get show metadata from first episode or dedicated show entry
            show_key = f"show:{show_name}"
            show_metadata = self.metadata.get(show_key, {})
            if not show_metadata:
                # Fallback to first episode's metadata if no show metadata exists
                first_season = next(iter(seasons.values()))
                first_episode = first_season[0]
                show_metadata = first_episode.get('metadata', {})
            
            # Create show card
            card = create_poster_card(
                show_name,
                show_metadata,
                lambda _, s=show_name, seas=seasons: self.show_episodes(s, seas),
                is_show=True
            )
            self.shows_box.append(card)

    def show_movie_details(self, movie):
        # Create item view using template
        item_view = VideohItem(self, movie)
        
        # Create navigation page
        page = Adw.NavigationPage(
            title=movie.get('metadata', {}).get('title', movie['title']),
            child=item_view
        )
        
        self.navigation_view.push(page)

    def edit_metadata(self, movie, key, current_value):
        dialog = Adw.MessageDialog(
            transient_for=self,
            title=_("Edit {}").format(key.title()),
            body=_("Enter new value:")
        )
        
        # Add entry for new value
        entry = Gtk.Entry()
        entry.set_text(str(current_value))
        dialog.set_extra_child(entry)
        
        # Add dialog buttons
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        
        # Handle response
        def on_response(dialog, response):
            if response == "save":
                new_value = entry.get_text()
                metadata = movie.get('metadata', {}).copy()
                metadata[key] = new_value
                self.update_metadata(movie['path'], metadata)
            dialog.close()
            
        dialog.connect("response", on_response)
        dialog.present()

    def edit_poster(self, movie):
        # Create file chooser
        file_chooser = Gtk.FileChooserNative.new(
            title=_("Choose Poster Image"),
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label=_("Open"),
            cancel_label=_("Cancel")
        )
        
        # Add file filters
        filter_images = Gtk.FileFilter()
        filter_images.set_name(_("Image files"))
        filter_images.add_mime_type("image/jpeg")
        filter_images.add_mime_type("image/png")
        file_chooser.add_filter(filter_images)  # Changed from set_filters to add_filter
        
        def on_response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                file_path = dialog.get_file().get_path()
                # Copy image to cache directory
                safe_title = "".join(c for c in movie['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
                poster_filename = f"{safe_title}.jpg"
                poster_path = self.cache_dir / "posters" / poster_filename
                
                # Create posters directory if it doesn't exist
                (self.cache_dir / "posters").mkdir(exist_ok=True)
                
                # Convert and save image
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(file_path)
                    pixbuf = pixbuf.scale_simple(200, 300, GdkPixbuf.InterpType.BILINEAR)
                    pixbuf.savev(str(poster_path), "jpeg", [], [])
                    
                    # Update metadata
                    metadata = movie.get('metadata', {}).copy()
                    metadata['poster'] = str(poster_path)
                    self.update_metadata(movie['path'], metadata)
                    
                    # Show success toast
                    toast = Adw.Toast.new(_("Poster updated successfully"))
                    toast.set_timeout(3)
                    self.toast_overlay.add_toast(toast)
                    
                except Exception as e:
                    error_dialog = Adw.MessageDialog.new(
                        self,
                        _("Error"),
                        _("Failed to update poster: {}").format(str(e))
                    )
                    error_dialog.add_response("ok", _("OK"))
                    error_dialog.present()
        
        file_chooser.connect("response", on_response)
        file_chooser.show()

    def on_movie_activated(self, movie):
        item_window = VideohItem(self, movie)
        item_window.present()

    def on_movie_clicked(self, gesture, n_press, x, y, movie):
        if n_press == 1:  # Only respond to single clicks
            self.show_movie_details(movie)

    def show_video(self, video_path, name):
        """Show video in player view"""
        player_view = VideohPlayer(self, video_path)
        page = Adw.NavigationPage(
            title=name,
            child=player_view
        )
        self.navigation_view.push(page)

    def show_episodes(self, show_name, seasons):
        """Show episodes list for a TV show"""
        episodes_view = EpisodesUI(self, show_name, seasons)
        page = Adw.NavigationPage(
            title=show_name,
            child=episodes_view
        )
        self.navigation_view.push(page)

    def on_show_clicked(self, gesture, n_press, x, y, show_name, seasons):
        if n_press == 1:  # Only respond to single clicks
            self.show_episodes(show_name, seasons)

    def _title_matches(self, file_title, imdb_title):
        """Compare file name with IMDb title, ignoring case and special characters"""
        def clean_title(title):
            return ''.join(c for c in title if c.isalnum())
        
        return clean_title(file_title) == clean_title(imdb_title)

    def _get_episode_number(self, filename):
        """Extract episode number from filename"""
        try:
            match = re.search(r'[Ss]\d+[Ee](\d+)|[Ee](\d+)|(\d+)$', filename)
            if match:
                return int(match.group(1) or match.group(2) or match.group(3))
        except Exception as e:
            print(f"Error extracting episode number from {filename}: {e}")  # Debug print
        return None

    def get_episode_metadata(self, episode_path):
        """Get combined show and episode metadata"""
        metadata = self.metadata.get(episode_path, {})
        if metadata.get('is_episode'):
            show_key = f"show:{metadata.get('show_name')}"
            show_metadata = self.metadata.get(show_key, {})
            # Merge show and episode metadata
            return {**show_metadata, **metadata}
        return metadata

    def _show_error_dialog(self, error_message):
        """Show error dialog for metadata fetch failures"""
        dialog = Adw.MessageDialog.new(
            self,
            _("Error"),
            _("Failed to fetch metadata: {}").format(error_message)
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()
        return False

    def get_imdb(self):
        """Get IMDb API client"""
        try:
            return IMDb()
        except Exception as e:
            self._show_error_dialog(f"Failed to initialize IMDb client: {e}")
            return None

    def get_tvmaze(self):
        """Get TVMaze API client"""
        try:
            return TVMaze()
        except Exception as e:
            self._show_error_dialog(f"Failed to initialize TVMaze client: {e}")
            return None

    def on_view_sorting(self, action, param):
        """Handle sorting action"""
        sort_type = param.get_string()
        
        def sort_items(items, key_func):
            return sorted(items, key=key_func)
        
        if sort_type == 'az':
            # Sort by title
            self.movies = sort_items(self.movies, 
                lambda x: x.get('metadata', {}).get('title', x['title']).lower())
            self.shows = dict(sorted(self.shows.items(), 
                key=lambda x: x[0].lower()))
        
        elif sort_type == 'year':
            # Sort by year
            self.movies = sort_items(self.movies,
                lambda x: x.get('metadata', {}).get('year', '0'))
            self.shows = dict(sorted(self.shows.items(),
                key=lambda x: self.metadata.get(f"show:{x[0]}", {}).get('year', '0')))
        
        elif sort_type == 'rating':
            # Sort by rating
            self.movies = sort_items(self.movies,
                lambda x: float(x.get('metadata', {}).get('rating', 0) or 0))
            self.shows = dict(sorted(self.shows.items(),
                key=lambda x: float(self.metadata.get(f"show:{x[0]}", {}).get('rating', 0) or 0)))
        
        # Update UI with new sorting
        self.populate_ui()

    def on_show_search_toggled(self, button):
        """Toggle search bar visibility"""
        self.search_bar.set_search_mode(button.get_active())
        if button.get_active():
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text("")

    def on_search_changed(self, *args):
        """Handle search text changes"""
        search_text = self.search_entry.get_text().lower()
        search_mode = self.search_mode.get_selected()  # 0 for title, 1 for genre
        
        # Clear existing content
        self.movies_box.remove_all()
        self.shows_box.remove_all()
        
        if not search_text:
            # If search is empty, show all items
            self.populate_ui()
            return
            
        # Filter movies
        for movie in self.movies:
            metadata = movie.get('metadata', {})
            if search_mode == 0:  # Title search
                title = metadata.get('title', movie['title']).lower()
                if search_text in title:
                    self._add_movie_to_ui(movie)
            else:  # Genre search
                genres = [g.lower() for g in metadata.get('genres', [])]
                if any(search_text in genre for genre in genres):
                    self._add_movie_to_ui(movie)
        
        # Filter shows
        for show_name, seasons in self.shows.items():
            show_key = f"show:{show_name}"
            show_metadata = self.metadata.get(show_key, {})
            
            if search_mode == 0:  # Title search
                if search_text in show_name.lower():
                    self._add_show_to_ui(show_name, seasons, show_metadata)
            else:  # Genre search
                genres = [g.lower() for g in show_metadata.get('genres', [])]
                if any(search_text in genre for genre in genres):
                    self._add_show_to_ui(show_name, seasons, show_metadata)

    def _add_movie_to_ui(self, movie):
        """Helper to add a single movie to the UI"""
        title = movie.get('metadata', {}).get('title', movie['title'])
        card = self._create_poster_card(
            title,
            movie.get('metadata', {}),
            lambda _, m=movie: self.show_movie_details(m)
        )
        self.movies_box.append(card)

    def _add_show_to_ui(self, show_name, seasons, metadata):
        """Helper to add a single show to the UI"""
        card = self._create_poster_card(
            show_name,
            metadata,
            lambda _, s=show_name, seas=seasons: self.show_episodes(s, seas),
            is_show=True
        )
        self.shows_box.append(card)

    def _create_poster_card(self, title, metadata, on_click, is_show=False):
        """Extract poster card creation to reusable method"""
        # Move the existing poster card creation code here
        # This is the code from your populate_ui method's create_poster_card function
        overlay = Gtk.Overlay()
        overlay.add_css_class('poster-box')
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class('card')
        
        # Add poster image
        poster = metadata.get('poster')
        if poster and Path(poster).exists():
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                poster, 200, 300, False)
            image = Gtk.Picture.new_for_pixbuf(pixbuf)
        else:
            # Use different fallback icons for shows and movies
            icon_name = "video-television" if is_show else "image-missing"
            image = Gtk.Image.new_from_icon_name(icon_name)
            image.set_pixel_size(200)
        
        image.add_css_class('poster-image')
        box.append(image)
        
        # Add title label
        label = Gtk.Label(label=title)
        label.set_wrap(True)
        label.set_max_width_chars(20)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.add_css_class('heading')
        label.add_css_class('poster-label')
        box.append(label)
        
        # Add info button overlay
        info_button = Gtk.Button()
        info_button.set_icon_name('info-outline-symbolic' if not is_show else 'view-list-symbolic')
        info_button.add_css_class('circular')
        info_button.add_css_class('osd')
        info_button.set_valign(Gtk.Align.START)
        info_button.set_halign(Gtk.Align.END)
        info_button.set_margin_top(6)
        info_button.set_margin_end(6)
        
        # Connect info button click
        info_button.connect('clicked', on_click)
        
        # Add main box and button to overlay
        overlay.set_child(box)
        overlay.add_overlay(info_button)
        
        # Make box clickable
        click = Gtk.GestureClick.new()
        click.connect('pressed', lambda g, n, x, y: on_click(None))
        box.add_controller(click)
        
        return overlay
