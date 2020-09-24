#!/usr/bin/env python

################################################################################
# Rendered math (MathJax) with Slack's desktop client
################################################################################
#
# Slack (https://slack.com) does not display rendered math. This script
# injects MathJax (https://www.mathjax.org) into Slack's desktop client,
# which allows you to write nice-looking inline- and display-style math
# using familiar TeX/LaTeX syntax.
#
# https://github.com/fsavje/math-with-slack
#
# MIT License
# Copyright 2017-2019 Fredrik Savje
# Copyright 2020-2021 Cambridge Yang

################################################################################

# Python 2.7 and 3

from __future__ import print_function

import argparse
import json
import os
import glob
import shutil
import struct
import sys
import time
import logging
import distutils.version
import collections
import operator
import functools
import sys
import os
import stat
import math
import tarfile
import tempfile

try:
  import urllib.request as urllib_request  # Python 3
except:
  import urllib as urllib_request  # Python 2

# ssl is added for Windows and possibly Mac to avoid ssl
# certificate_verify_failed error
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

LooseVersion = distutils.version.LooseVersion

# Math with Slack version

mws_version = '0.4.1.1'


class AsarArchive:
  """Represents a single *.asar file."""
  LOGGER = logging.getLogger("AsarArchive")

  def __init__(self, filename, asarfile, files, baseoffset):
    """Initializes a new instance of the :see AsarArchive class.

    Args:
        filename (str):
            The path to the *.asar file to read/write from/to.
        asarfile (File):
            An open *.asar file object.
        files (dict):
            Dictionary of files contained in the archive.
            (The header that was read from the file).
        baseoffset (int):
            Base offset, indicates where in the file the header ends.
    """
    self.filename = filename
    self.asarfile = asarfile
    self.files = files
    self.baseoffset = baseoffset

  def extract(self, destination):
    """Extracts the contents of the archive to the specifed directory.

    Args:
        destination (str):
            Path to an empty directory to extract the files to.
    """

    if os.path.exists(destination):
      raise OSError(20, 'Destination exists', destination)

    self.__extract_directory('.', self.files['files'], destination)

  def __extract_directory(self, path, files, destination):
    """Extracts a single directory to the specified directory on disk.

    Args:
        path (str):
            Relative (to the root of the archive) path of the directory
            to extract.
        files (dict):
            A dictionary of files from a *.asar file header.
        destination (str):
            The path to extract the files to.
    """

    # assures the destination directory exists
    destination_path = os.path.join(destination, path)
    if not os.path.exists(destination_path):
      os.makedirs(destination_path)
    for name, contents in files.items():
      item_path = os.path.join(path, name)

      # objects that have a 'files' member are directories,
      # recurse into them
      if 'files' in contents:
        self.__extract_directory(item_path, contents['files'], destination)

        continue

      self.__extract_file(item_path, contents, destination)

  @staticmethod
  def __is_unpacked(fileinfo):
    return fileinfo.get('unpacked', False)

  def __extract_file(self, path, fileinfo, destination):
    """Extracts the specified file to the specified destination.

    Args:
        path (str):
            Relative (to the root of the archive) path of the
            file to extract.
        fileinfo (dict):
            Dictionary containing the offset and size of the file
            (Extracted from the header).
        destination (str):
            Directory to extract the archive to.
    """
    if self.__is_unpacked(fileinfo):
      self.__copy_unpacked(path, destination)
      return

    self.asarfile.seek(self.__absolute_offset(fileinfo['offset']))

    # TODO: read in chunks, ain't going to read multiple GB's in memory
    contents = self.asarfile.read(fileinfo['size'])

    destination_path = os.path.join(destination, path)

    with open(destination_path, 'wb') as fp:
      fp.write(contents)

    if sys.platform != 'win32' and fileinfo.get('executable', False):
      os.chmod(destination_path,
               os.stat(destination_path).st_mode | stat.S_IEXEC)

    self.LOGGER.debug('Extracted %s to %s', path, destination_path)

  def __copy_unpacked(self, path, destination):
    """Copies a file that was already extracted to the destination directory.

    Args:
        path (str):
            Relative (to the root of the archive) of the file to copy.
        destination (str):
            Directory to extract the archive to.
    """

    unpacked_dir = self.filename + '.unpacked'
    if not os.path.isdir(unpacked_dir):
      self.LOGGER.warn('Failed to copy extracted file %s, no extracted dir',
                       path)

      return

    source_path = os.path.join(unpacked_dir, path)

    if not os.path.exists(source_path):
      self.LOGGER.warn('Failed to copy extracted file %s, does not exist', path)

      return

    destination_path = os.path.join(destination, path)
    shutil.copyfile(source_path, destination_path)

  def __absolute_offset(self, offset):
    """Converts the specified relative offset into an absolute offset.
    
    Offsets specified in the header are relative to the end of the header.
    Args:
        offset (int):
            The relative offset to convert to an absolute offset.
    Returns (int):
        The specified relative offset as an absolute offset.
    """

    return int(offset) + self.baseoffset

  def get_unpackeds(self):
    """Gets all the unpacked files."""
    for path, fileinfo in self.__walk_fileinfos(self.files, root="."):
      if self.__is_unpacked(fileinfo):
        yield os.path.relpath(path, ".")

  def __enter__(self):
    """When the `with` statements opens."""

    return self

  def __exit__(self, type, value, traceback):
    """When the `with` statement ends."""

    if not self.asarfile:
      return

    self.asarfile.close()
    self.asarfile = None

  @staticmethod
  def __roundup(val, divisor):
    return int(math.ceil((float(val) / divisor)) * divisor)

  @classmethod
  def open(cls, filename):
    """Opens a *.asar file and constructs a new :see AsarArchive instance.
    
    Args:
        filename (str):
            Path to the *.asar file to open for reading.
    Returns (AsarArchive):
        An insance of of the :AsarArchive class or None if reading failed.
    """

    asarfile = open(filename, 'rb')

    (header_data_size, json_binary_size, json_data_size,
     json_string_size) = struct.unpack('<4I', asarfile.read(16))
    assert header_data_size == 4
    assert json_binary_size == json_data_size + 4
    json_header_bytes = asarfile.read(json_string_size).decode('utf-8')
    files = json.loads(json_header_bytes)
    baseoffset = AsarArchive.__roundup(16 + json_string_size, 4)
    return cls(filename, asarfile, files, baseoffset)

  @staticmethod
  def __dir_to_fileinfos(directory, unpackeds=tuple()):
    fileinfos = {'.': {'files': {}}}
    offset = 0
    for root, subdirs, files in os.walk(directory, topdown=True):
      parts = os.path.normpath(os.path.relpath(root,
                                               directory)).split(os.path.sep)
      if root != directory:
        parts = ['.'] + parts
      dirinfo = functools.reduce(lambda dirinfo, part: dirinfo[part]['files'],
                                 parts, fileinfos)
      for file in files:
        file_path = os.path.join(root, file)
        file_size = os.path.getsize(file_path)
        if os.path.relpath(file_path, directory) in unpackeds:
          fileinfo = {'size': file_size, 'unpacked': True}
        else:
          fileinfo = {'size': file_size, 'offset': str(offset)}
          offset += file_size
        if sys.platform != 'win32' and (os.stat(file_path).st_mode & 0o100):
          fileinfo['executable'] = True
        dirinfo[file] = fileinfo

      for subdir in subdirs:
        dirinfo[subdir] = {'files': {}}

    return fileinfos['.']

  @staticmethod
  def __walk_fileinfos(fileinfos, root="."):
    for name, fileinfo in fileinfos["files"].items():
      sub_path = os.path.join(root, name)
      if 'files' in fileinfo:
        # is directory
        for v in AsarArchive.__walk_fileinfos(fileinfo, root=sub_path):
          yield v
      else:
        yield sub_path, fileinfo

  @staticmethod
  def pack(directory, out_asarfile, unpackeds=tuple()):
    fileinfos = AsarArchive.__dir_to_fileinfos(directory, unpackeds=unpackeds)
    json_header = json.dumps(fileinfos, sort_keys=True, separators=(',', ':'))
    json_header_bytes = json_header.encode('utf-8')
    header_string_size = len(json_header_bytes)
    data_size = 4
    aligned_size = AsarArchive.__roundup(header_string_size, data_size)
    header_size = aligned_size + 8
    header_object_size = aligned_size + data_size
    out_asarfile.seek(0)
    out_asarfile.write(
        struct.pack('<4I', data_size, header_size, header_object_size,
                    header_string_size))
    out_asarfile.write(json_header_bytes + b'\0' *
                       (aligned_size - header_string_size))
    baseoffset = AsarArchive.__roundup(header_string_size + 16, 4)
    for path, fileinfo in AsarArchive.__walk_fileinfos(fileinfos):
      if AsarArchive.__is_unpacked(fileinfo):
        continue
      with open(os.path.join(directory, path), 'rb') as fp:
        out_asarfile.seek(int(fileinfo['offset']) + baseoffset)
        out_asarfile.write(fp.read())


def parse_args():

  Parser = gooey.GooeyParser if USE_GUI else argparse.ArgumentParser

  def add_argument(*args, **kwargs):
    if not USE_GUI:
      kwargs.pop('widget', None)
    return parser.add_argument(*args, **kwargs)

  parser = Parser(prog='math-with-slack',
                  description='Inject Slack with MathJax.')
  if USE_GUI:
    # choose_slack_group = parser.add_argument_group("Choose Slack App")
    group = parser.add_mutually_exclusive_group(
        required=True, gooey_options={'initial_selection': 0})
    search_paths = get_search_path_by_platform(sys.platform)
    candidate_files = find_candidate_files(search_paths, "app.asar")
    group.add_argument(
        '--app-file-from-auto-search',
        help="Path to Slack's 'app.asar' file detected automatically.",
        choices=candidate_files,
        default=candidate_files[0])
    group.add_argument('--app-file',
                       help="Manually provide path to Slack's 'app.asar' file.",
                       widget='FileChooser')
  else:
    parser.add_argument('-a',
                        '--app-file',
                        help="Path to Slack's 'app.asar' file.")

  add_argument('--mathjax-url',
               help='Url to download mathjax release.',
               default='https://registry.npmjs.org/mathjax/-/mathjax-3.1.0.tgz')
  add_argument(
      '--mathjax-tex-options',
      type=str,
      help='String of TeX input processor options '
      '(See http://docs.mathjax.org/en/latest/options/input/tex.html).',
      default='default\n11',
      widget="Textarea")
  add_argument('-u',
               '--uninstall',
               action='store_true',
               help='Removes injected MathJax code.')
  if not USE_GUI:
    add_argument('--version',
                 action='version',
                 version='%(prog)s ' + mws_version)
  return parser.parse_args()


USE_GUI = (len(sys.argv) >= 2 and
           sys.argv[1] == "gui") or '--ignore-gooey' in sys.argv
if USE_GUI:
  import gooey
  if sys.argv[1] == "gui":
    del sys.argv[1]
  parse_args = gooey.Gooey(
      default_size=(610, 700),
      optional_cols=1,
      menu=[{
          'name':
              'math-with-slack',
          'items': [{
              'type': 'AboutDialog',
              'menuTitle': 'About',
              'name': 'math-with-slack',
              'description': 'Injects MathJax to Slack, GUI powered by Gooey.',
              'version': mws_version,
              'license': 'MIT'
          }]
      }],
      progress_regex=r"Downloading MathJax...(\d+)%")(parse_args)
else:
  parse_args = parse_args


def exprint(*args, **kwargs):
  print(*args, file=sys.stderr, **kwargs)
  sys.exit(1)


default_search_paths_by_platforms = {
    'darwin': ['/Applications/Slack.app/Contents/Resources/'],
    'linux': [
        '/usr/lib/slack/resources/',
        '/usr/local/lib/slack/resources/',
        '/opt/slack/resources/',
        '/mnt/c/Users/*/AppData/Local/slack/*/resources/',
    ],
    'win32': ['c:/Users/*/AppData/Local/slack/*/resources/']
}


def get_search_path_by_platform(
    os, search_paths_lookup=default_search_paths_by_platforms):
  platform = sys.platform
  if platform.startswith('linux'):
    platform = 'linux'
  return search_paths_lookup[platform]


def find_candidate_files(path_globs, filename):
  candidates = []
  for path_glob in path_globs:
    candidates += glob.glob(os.path.join(path_glob, filename))
  candidates = [c for c in candidates if os.path.isfile(c)]
  candidates = sorted(candidates,
                      key=lambda c: os.path.getctime(c),
                      reverse=True)
  return candidates


def display_choose_from_menu(candidates, header="", prompt=""):
  print(header)
  for i, candidate in enumerate(candidates):
    if i == 0:
      candidate += " <== (default)"
    print("{}) {}".format(i, candidate))
  choice = input(prompt).lower()
  choice = "".join(choice.split())  # remote whitespace
  choice = choice.strip(")")  # remove trailing ')'
  try:
    if choice in ("", "y", "yes"):
      choice = 0
    else:
      choice = int(choice)
    return candidates[choice]
  except:
    exprint("Invalid choice. Please restart script.")


def find_app_in_search_paths_cmd(search_paths):
  candidate_app_asars = find_candidate_files(search_paths, filename='app.asar')
  if len(candidate_app_asars) == 0:
    exprint(("Could not find Slack's app.asar file under {}. "
             "Please manually provide path (see --help)").format(search_paths))
  if len(candidate_app_asars) == 1:
    app_path = candidate_app_asars[0]
  else:
    app_path = display_choose_from_menu(
        candidate_app_asars,
        header="Several versions of Slack are installed.",
        prompt="Choose from above:")

  return app_path


def extract_slack_asar(tmpdir, app_path):
  slack_asar_extracted_dir = os.path.join(tmpdir, "slack.extracted")
  with AsarArchive.open(app_path) as slack_asar:
    slack_asar.extract(slack_asar_extracted_dir)
    unpackeds = set(slack_asar.get_unpackeds())
  return slack_asar_extracted_dir, unpackeds


def recover_from_backup(slack_asar_extracted_dir, app_path, app_backup_path):
  if os.path.isfile(os.path.join(slack_asar_extracted_dir, "MWSINJECT")):
    if not os.path.isfile(app_backup_path):
      exprint('Found injected code without backup. Please re-install Slack.')
    try:
      os.remove(app_path)
      shutil.move(app_backup_path, app_path)
    except Exception as e:
      print(e)
      exprint('Cannot remove previously injected code. '
              'Make sure the script has write permissions.')


def remove_backup(app_backup_path):
  if os.path.isfile(app_backup_path):
    try:
      os.remove(app_backup_path)
    except Exception as e:
      print(e)
      exprint('Cannot remove old backup. '
              'Make sure the script has write permissions.')


def make_backup(app_path, app_backup_path):
  try:
    shutil.copy(app_path, app_backup_path)
  except Exception as e:
    print(e)
    exprint('Cannot make backup. Make sure the script has write permissions.')


def read_slack_version(slack_asar_extracted_dir):
  print(slack_asar_extracted_dir)
  with open(os.path.join(slack_asar_extracted_dir, "package.json"), "r") as fp:
    return LooseVersion(json.load(fp)['version'])


def download_mathjax(tmpdir, mathjax_url):
  """Download MathJax. 
  Currently assumes downloaded file is a tar called package.tar.
  """

  def get_reporthook():

    class report:
      start_time = None
      progress_size = None

    def reporthook(count, block_size, total_size):
      if count == 0:
        report.progress_size = 0
        report.start_time = time.time(
        ) - 1e-6  # also offset a bit so we don't run into divide by zero.
        return
      duration = time.time() - report.start_time
      report.progress_size += block_size
      if report.progress_size >= total_size:
        report.progress_size = total_size
      try:
        speed = report.progress_size / (1024 * duration)
        percent = int(report.progress_size * 100 / total_size)
      except ZeroDivisionError:
        speed, percent = 0, 0
      sys.stdout.write(
          "\rDownloading MathJax...{:3d}%, {:3.1f} MB / {:3.1f} MB,"
          " {:6.1f} KB/s, {:3.1f} sec".format(
              percent, report.progress_size / (1024 * 1024),
              total_size / (1024 * 1024), speed, duration))
      if report.progress_size >= total_size:
        sys.stdout.write("\n")
      sys.stdout.flush()

    return reporthook

  mathjax_tar_name, _ = urllib_request.urlretrieve(mathjax_url, [],
                                                   get_reporthook())
  mathjax_tmp_dir = os.path.join(tmpdir, "mathjax")
  mathjax_tar = tarfile.open(mathjax_tar_name)
  mathjax_tar.extractall(path=mathjax_tmp_dir)
  mathjax_tar.close()
  mathjax_dir = os.path.join(mathjax_tmp_dir, "package")
  return mathjax_dir


inject_code = ('\n\n// math-with-slack ' + mws_version + '''
// Inject MathJax 3.
// Credit to initial implementation: https://github.com/fsavje/math-with-slack
document.addEventListener('DOMContentLoaded', function() {

  function typeset(elements) {
    if(elements.length) {
        const MathJax = window.MathJax;
        MathJax.startup.promise = MathJax.startup.promise
          .then(() => { return MathJax.typesetPromise(elements); })
          .catch((err) => console.log('Typeset failed: ' + err.message));
        return MathJax.startup.promise;
    }
  }

  window.MathJax = {
    options: {
        skipHtmlTags: [
            'script', 'noscript', 'style', 'textarea', 'pre',
            'code', 'annotation', 'annotation-xml'
        ]
    },
    loader: {
        paths: {mathjax: 'mathjax/es5'},
        source: {},
        require: require,
        load: [
            'input/tex-full',
            'output/svg',
            '[tex]/noerrors', 
            '[tex]/noundefined', 
        ]
    },
    tex: $MATHJAX_TEX_OPTIONS$,
    startup: {
      ready: () => {
        MathJax = window.MathJax;
        MathJax.startup.defaultReady();

        // Disable some menu option that will cause us to crash
        MathJax.startup.document.menu.menu.findID('Settings', 'Renderer').disable();
        MathJax.startup.document.menu.menu.findID('Accessibility').disable();

        // Observer for when an element needs to be typeset
        var entry_observer = new IntersectionObserver(
          (entries, observer) => {
            var appearedEntries = entries.filter((entry) => entry.intersectionRatio > 0);
            if(appearedEntries.length) {
                typeset(appearedEntries.map((entry) => entry.target));
            }
          },
          { root: document.body }
        );

        // observer for elements are first inserted into the DOM.
        // We delay elements that require typesetting to the intersection observer
        function enque_typeset() {
            var messages = document.querySelectorAll(
            'span.c-message__body, span.c-message_kit__text, div.p-rich_text_block, span.c-message_attachment__text');
            var to_typeset = [];
            for (var i = 0; i < messages.length; i++) {
                var msg = messages[i];
                if(!msg.ready) {
                    msg.ready = true;
                    entry_observer.observe(msg);
                }
            }
        }
        observer = new MutationObserver(enque_typeset);
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
        enque_typeset();
      }
    },
  };

  // Import mathjax
  $MATHJAX_STUB$
});
''').encode('utf-8')


def get_injected_file_path(slack_asar_extracted_dir):
  slack_version = read_slack_version(slack_asar_extracted_dir)
  if LooseVersion('4.3') <= slack_version < LooseVersion('4.4'):
    injected_file_name = 'main-preload-entry-point.bundle.js'
  elif LooseVersion('4.4') <= slack_version:
    injected_file_name = 'preload.bundle.js'
  else:
    exprint("Unsupported Slack Version {}.".format(slack_version))
  return os.path.join(slack_asar_extracted_dir, "dist", injected_file_name)


def run_injection(slack_asar_extracted_dir,
                  mathjax_dir,
                  mathjax_tex_options="default",
                  inject_code=inject_code):
  injected_file_path = get_injected_file_path(slack_asar_extracted_dir)
  mathjax_src_path = os.path.join(mathjax_dir, "es5", "tex-svg-full.js")
  with open(mathjax_src_path, "r+") as mathjax_src_file:
    mathjax_src = mathjax_src_file.read().encode('utf-8')
  inject_code = inject_code.replace(b"$MATHJAX_STUB$", mathjax_src)

  if mathjax_tex_options == "default":
    mathjax_tex_options = """{
        packages: {'[+]': ['noerrors', 'noundefined']},
        inlineMath: [['$', '$']],
        displayMath: [['$$', '$$']],
      }"""
  inject_code = inject_code.replace(b"$MATHJAX_TEX_OPTIONS$",
                                    mathjax_tex_options.encode('utf-8'))

  with open(injected_file_path, "wb+") as injected_file:
    current_src = injected_file.read()
    new_src = current_src + inject_code
    injected_file.seek(0)
    injected_file.write(new_src)


def run_injection_flow():

  args = parse_args()

  if args.app_file is not None:
    app_path = args.app_file
    if not os.path.isfile(app_path):
      exprint('Cannot find Slack at ' + app_path)
  else:
    search_paths = get_search_path_by_platform(sys.platform)
    app_path = find_app_in_search_paths_cmd(search_paths)

  print('Using Slack installation at: {}'.format(app_path))
  app_backup_path = app_path + '.mwsbak'

  tmpdir = tempfile.mkdtemp()

  slack_asar_extracted_dir, unpackeds = extract_slack_asar(tmpdir, app_path)
  recover_from_backup(slack_asar_extracted_dir, app_path, app_backup_path)
  remove_backup(app_backup_path)

  if args.uninstall:
    print('Uninstall successful. Please restart Slack.')
    sys.exit(0)

  make_backup(app_path, app_backup_path)

  mathjax_dir = download_mathjax(tmpdir, args.mathjax_url)
  run_injection(slack_asar_extracted_dir,
                mathjax_dir,
                mathjax_tex_options=args.mathjax_tex_options)

  with open(app_path, "wb+") as f:
    AsarArchive.pack(slack_asar_extracted_dir, f, unpackeds=unpackeds)

  shutil.rmtree(tmpdir)
  print('Install successful. Please restart Slack.')
  sys.exit(0)


if __name__ == "__main__":
  run_injection_flow()

# References

# https://github.com/electron/node-chromium-pickle-js
# https://chromium.googlesource.com/chromium/src/+/master/base/pickle.h
# https://github.com/electron/asar
# https://github.com/electron-archive/node-chromium-pickle
# https://github.com/leovoel/BeautifulDiscord/tree/master/beautifuldiscord
