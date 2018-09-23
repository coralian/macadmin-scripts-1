#!/usr/bin/python
# encoding: utf-8
#
# Copyright 2017 Greg Neagle.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Thanks to Tim Sutton for ideas, suggestions, and sample code.
#

'''installinstallmacos.py
A tool to download the parts for an Install macOS app from Apple's
softwareupdate servers and install a functioning Install macOS app onto an
empty disk image'''


import argparse
import gzip
import os
import plistlib
import subprocess
import re
import sys
import time
import urlparse
import xattr
from xml.dom import minidom
from xml.parsers.expat import ExpatError
from operator import itemgetter
from distutils.version import LooseVersion


DEFAULT_SUCATALOG = (
    'https://swscan.apple.com/content/catalogs/others/'
    'index-10.13seed-10.13-10.12-10.11-10.10-10.9'
    '-mountainlion-lion-snowleopard-leopard.merged-1.sucatalog.gz')

SEED_CATALOGS_PLIST = (
    '/System/Library/PrivateFrameworks/Seeding.framework/Versions/Current/'
    'Resources/SeedCatalogs.plist'
)


def get_board_id():
    '''Gets the local system board ID'''
    ioreg_cmd = ['ioreg', '-p', 'IODeviceTree', '-r', '-n', '/', '-d', '1']
    try:
        ioreg_output = subprocess.check_output(ioreg_cmd).splitlines()
        for line in ioreg_output:
            if 'board-id' in line:
                board_id = line.split(" ")[-1]
                board_id = board_id[board_id.find('<"')+2:board_id.find('">')]
                return board_id
    except subprocess.CalledProcessError, err:
        raise ReplicationError(err)


def get_hw_model():
    '''Gets the local system ModelIdentifier'''
    sysctl_cmd = ['/usr/sbin/sysctl', 'hw.model']
    try:
        sysctl_output = subprocess.check_output(sysctl_cmd)
        hw_model = sysctl_output.split(" ")[-1].split("\n")[0]
    except subprocess.CalledProcessError, err:
        raise ReplicationError(err)
    return hw_model


def get_current_build():
    '''Gets the local system build'''
    sw_vers_cmd = ['/usr/bin/sw_vers']
    try:
        sw_vers_output = subprocess.check_output(sw_vers_cmd).splitlines()
        for line in sw_vers_output:
            if 'BuildVersion' in line:
                os_build = line.split("\t")[-1]
                return os_build
    except subprocess.CalledProcessError, err:
        raise ReplicationError(err)
    return os_build


    '''Returns a seeding program name based on the sucatalog_url'''
    try:
        seed_catalogs = plistlib.readPlist(SEED_CATALOGS_PLIST)
        for key, value in seed_catalogs.items():
            if sucatalog_url == value:
                return key
    except (OSError, ExpatError, AttributeError, KeyError):
        return None


def get_seed_catalog():
    '''Returns the developer seed sucatalog'''
    try:
        seed_catalogs = plistlib.readPlist(SEED_CATALOGS_PLIST)
        return seed_catalogs.get('DeveloperSeed', DEFAULT_SUCATALOG)
    except (OSError, ExpatError, AttributeError, KeyError):
        return DEFAULT_SUCATALOG


def make_sparse_image(volume_name, output_path):
    '''Make a sparse disk image we can install a product to'''
    cmd = ['/usr/bin/hdiutil', 'create', '-size', '8g', '-fs', 'HFS+',
           '-volname', volume_name, '-type', 'SPARSE', '-plist', output_path]
    try:
        output = subprocess.check_output(cmd)
    except subprocess.CalledProcessError, err:
        print >> sys.stderr, err
        exit(-1)
    try:
        return plistlib.readPlistFromString(output)[0]
    except IndexError, err:
        print >> sys.stderr, 'Unexpected output from hdiutil: %s' % output
        exit(-1)
    except ExpatError, err:
        print >> sys.stderr, 'Malformed output from hdiutil: %s' % output
        print >> sys.stderr, err
        exit(-1)


def make_compressed_dmg(app_path, diskimagepath):
    """Returns path to newly-created compressed r/o disk image containing
    Install macOS.app"""

    print ('Making read-only compressed disk image containing %s...'
           % os.path.basename(app_path))
    cmd = ['/usr/bin/hdiutil', 'create', '-fs', 'HFS+',
           '-srcfolder', app_path, diskimagepath]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError, err:
        print >> sys.stderr, err
    else:
        print 'Disk image created at: %s' % diskimagepath


def mountdmg(dmgpath):
    """
    Attempts to mount the dmg at dmgpath and returns first mountpoint
    """
    mountpoints = []
    dmgname = os.path.basename(dmgpath)
    cmd = ['/usr/bin/hdiutil', 'attach', dmgpath,
           '-mountRandom', '/tmp', '-nobrowse', '-plist',
           '-owners', 'on']
    proc = subprocess.Popen(cmd, bufsize=-1,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (pliststr, err) = proc.communicate()
    if proc.returncode:
        print >> sys.stderr, 'Error: "%s" while mounting %s.' % (err, dmgname)
        return None
    if pliststr:
        plist = plistlib.readPlistFromString(pliststr)
        for entity in plist['system-entities']:
            if 'mount-point' in entity:
                mountpoints.append(entity['mount-point'])

    return mountpoints[0]


def unmountdmg(mountpoint):
    """
    Unmounts the dmg at mountpoint
    """
    proc = subprocess.Popen(['/usr/bin/hdiutil', 'detach', mountpoint],
                            bufsize=-1, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    (dummy_output, err) = proc.communicate()
    if proc.returncode:
        print >> sys.stderr, 'Polite unmount failed: %s' % err
        print >> sys.stderr, 'Attempting to force unmount %s' % mountpoint
        # try forcing the unmount
        retcode = subprocess.call(['/usr/bin/hdiutil', 'detach', mountpoint,
                                   '-force'])
        if retcode:
            print >> sys.stderr, 'Failed to unmount %s' % mountpoint


def install_product(dist_path, target_vol):
    '''Install a product to a target volume.
    Returns a boolean to indicate success or failure.'''
    cmd = ['/usr/sbin/installer', '-pkg', dist_path, '-target', target_vol]
    try:
        subprocess.check_call(cmd)
        return True
    except subprocess.CalledProcessError, err:
        print >> sys.stderr, err
        return False


class ReplicationError(Exception):
    '''A custom error when replication fails'''
    pass


def replicate_url(full_url, root_dir='/tmp',
                  show_progress=False, ignore_cache=False):
    '''Downloads a URL and stores it in the same relative path on our
    filesystem. Returns a path to the replicated file.'''

    path = urlparse.urlsplit(full_url)[2]
    relative_url = path.lstrip('/')
    relative_url = os.path.normpath(relative_url)
    local_file_path = os.path.join(root_dir, relative_url)
    if show_progress:
        options = '-fL'
    else:
        options = '-sfL'
    curl_cmd = ['/usr/bin/curl', options, '--create-dirs',
                '-o', local_file_path]
    if not ignore_cache and os.path.exists(local_file_path):
        curl_cmd.extend(['-z', local_file_path])
    curl_cmd.append(full_url)
    # print "Downloading %s..." % full_url
    try:
        subprocess.check_call(curl_cmd)
    except subprocess.CalledProcessError, err:
        raise ReplicationError(err)
    return local_file_path


def parse_server_metadata(filename):
    '''Parses a softwareupdate server metadata file, looking for information
    of interest.
    Returns a dictionary containing title, version, and description.'''
    title = ''
    vers = ''
    try:
        md_plist = plistlib.readPlist(filename)
    except (OSError, IOError, ExpatError), err:
        print >> sys.stderr, 'Error reading %s: %s' % (filename, err)
        return {}
    vers = md_plist.get('CFBundleShortVersionString', '')
    localization = md_plist.get('localization', {})
    preferred_localization = (localization.get('English') or
                              localization.get('en'))
    if preferred_localization:
        title = preferred_localization.get('title', '')

    metadata = {}
    metadata['title'] = title
    metadata['version'] = vers
    return metadata


def get_server_metadata(catalog, product_key, workdir, ignore_cache=False):
    '''Replicate ServerMetaData'''
    try:
        url = catalog['Products'][product_key]['ServerMetadataURL']
        try:
            smd_path = replicate_url(
                url, root_dir=workdir, ignore_cache=ignore_cache)
            return smd_path
        except ReplicationError, err:
            print >> sys.stderr, (
                'Could not replicate %s: %s' % (url, err))
            return None
    except KeyError:
        print >> sys.stderr, 'Malformed catalog.'
        return None


def parse_dist(filename):
    '''Parses a softwareupdate dist file, returning a dict of info of
    interest'''
    dist_info = {}
    try:
        dom = minidom.parse(filename)
    except ExpatError:
        print >> sys.stderr, 'Invalid XML in %s' % filename
        return dist_info
    except IOError, err:
        print >> sys.stderr, 'Error reading %s: %s' % (filename, err)
        return dist_info

    auxinfos = dom.getElementsByTagName('auxinfo')
    if not auxinfos:
        return dist_info
    auxinfo = auxinfos[0]
    key = None
    value = None
    children = auxinfo.childNodes
    # handle the possibility that keys from auxinfo may be nested
    # within a 'dict' element
    dict_nodes = [n for n in auxinfo.childNodes
                  if n.nodeType == n.ELEMENT_NODE and
                  n.tagName == 'dict']
    if dict_nodes:
        children = dict_nodes[0].childNodes
    for node in children:
        if node.nodeType == node.ELEMENT_NODE and node.tagName == 'key':
            key = node.firstChild.wholeText
        if node.nodeType == node.ELEMENT_NODE and node.tagName == 'string':
            value = node.firstChild.wholeText
        if key and value:
            dist_info[key] = value
            key = None
            value = None
    return dist_info


def get_board_ids(filename):
    '''Parses a softwareupdate dist file, returning a list of supported
    Board IDs'''
    supported_board_ids = ""
    with open(filename) as search:
        for line in search:
            line = line.rstrip()  # remove '\n' at end of line
            if 'boardIds' in line:
                supported_board_ids = line.split(" ")[-1][:-1]
                return supported_board_ids


def get_unsupported_models(filename):
    '''Parses a softwareupdate dist file, returning a list of non-supported
    ModelIdentifiers'''
    unsupported_models = ""
    with open(filename) as search:
        for line in search:
            line = line.rstrip()  # remove '\n' at end of line
            if 'nonSupportedModels' in line:
                unsupported_models = line.split(" ")[-1][:-1]
                return unsupported_models


def download_and_parse_sucatalog(sucatalog, workdir, ignore_cache=False):
    '''Downloads and returns a parsed softwareupdate catalog'''
    try:
        localcatalogpath = replicate_url(
            sucatalog, root_dir=workdir, ignore_cache=ignore_cache)
    except ReplicationError, err:
        print >> sys.stderr, 'Could not replicate %s: %s' % (sucatalog, err)
        exit(-1)
    if os.path.splitext(localcatalogpath)[1] == '.gz':
        with gzip.open(localcatalogpath) as f:
            content = f.read()
            try:
                catalog = plistlib.readPlistFromString(content)
                return catalog
            except ExpatError, err:
                print >> sys.stderr, (
                    'Error reading %s: %s' % (localcatalogpath, err))
                exit(-1)
    else:
        try:
            catalog = plistlib.readPlist(localcatalogpath)
            return catalog
        except (OSError, IOError, ExpatError), err:
            print >> sys.stderr, (
                'Error reading %s: %s' % (localcatalogpath, err))
            exit(-1)


def find_mac_os_installers(catalog):
    '''Return a list of product identifiers for what appear to be macOS
    installers'''
    mac_os_installer_products = []
    if 'Products' in catalog:
        product_keys = list(catalog['Products'].keys())
        for product_key in product_keys:
            product = catalog['Products'][product_key]
            try:
                if product['ExtendedMetaInfo'][
                        'InstallAssistantPackageIdentifiers'][
                            'OSInstall'] == 'com.apple.mpkg.OSInstall':
                    mac_os_installer_products.append(product_key)
            except KeyError:
                continue
    return mac_os_installer_products


def os_installer_product_info(catalog, workdir, ignore_cache=False):
    '''Returns a dict of info about products that look like macOS installers'''
    product_info = {}
    installer_products = find_mac_os_installers(catalog)
    for product_key in installer_products:
        product_info[product_key] = {}
        filename = get_server_metadata(catalog, product_key, workdir)
        product_info[product_key] = parse_server_metadata(filename)
        product = catalog['Products'][product_key]
        product_info[product_key]['PostDate'] = product['PostDate']
        distributions = product['Distributions']
        dist_url = distributions.get('English') or distributions.get('en')
        try:
            dist_path = replicate_url(
                dist_url, root_dir=workdir, ignore_cache=ignore_cache)
        except ReplicationError, err:
            print >> sys.stderr, 'Could not replicate %s: %s' % (dist_url, err)
        dist_info = parse_dist(dist_path)
        product_info[product_key]['DistributionPath'] = dist_path
        unsupported_models = get_unsupported_models(dist_path)
        product_info[product_key]['UnsupportedModels'] = unsupported_models
        board_ids = get_board_ids(dist_path)
        product_info[product_key]['BoardIDs'] = board_ids
        product_info[product_key].update(dist_info)

    return product_info


def replicate_product(catalog, product_id, workdir, ignore_cache=False):
    '''Downloads all the packages for a product'''
    product = catalog['Products'][product_id]
    for package in product.get('Packages', []):
        # TO-DO: Check 'Size' attribute and make sure
        # we have enough space on the target
        # filesystem before attempting to download
        if 'URL' in package:
            try:
                replicate_url(
                    package['URL'], root_dir=workdir,
                    show_progress=True, ignore_cache=ignore_cache)
            except ReplicationError, err:
                print >> sys.stderr, (
                    'Could not replicate %s: %s' % (package['URL'], err))
                exit(-1)
        if 'MetadataURL' in package:
            try:
                replicate_url(package['MetadataURL'], root_dir=workdir,
                              ignore_cache=ignore_cache)
            except ReplicationError, err:
                print >> sys.stderr, (
                    'Could not replicate %s: %s'
                    % (package['MetadataURL'], err))
                exit(-1)


def find_installer_app(mountpoint):
    '''Returns the path to the Install macOS app on the mountpoint'''
    applications_dir = os.path.join(mountpoint, 'Applications')
    for item in os.listdir(applications_dir):
        if item.endswith('.app'):
            return os.path.join(applications_dir, item)
    return None


def get_lowest_version(current_item, lowest_item):
    '''Compares versions between two values and returns the lowest value'''
    if LooseVersion(current_item) < LooseVersion(lowest_item):
        return current_item
    else:
        return lowest_item


def main():
    '''Do the main thing here'''

    print ('\n'
           'installinstallmacos.py - get macOS installers '
           'from the Apple software catalog'
           '\n')

    if os.getuid() != 0:
        sys.exit('This command requires root (to install packages), so please '
                 'run again with sudo or as root.')

    parser = argparse.ArgumentParser()
    parser.add_argument('--catalogurl', metavar='sucatalog_url',
                        default=get_seed_catalog(),
                        help='Software Update catalog URL.')
    parser.add_argument('--workdir', metavar='path_to_working_dir',
                        default='.',
                        help='Path to working directory on a volume with over '
                        '10G of available space. Defaults to current working '
                        'directory.')
    parser.add_argument('--compress', action='store_true',
                        help='Output a read-only compressed disk image with '
                        'the Install macOS app at the root. This is now the '
                        'default. Use --raw to get a read-write sparse image '
                        'with the app in the Applications directory.')
    parser.add_argument('--raw', action='store_true',
                        help='Output a read-write sparse image '
                        'with the app in the Applications directory. Requires '
                        'less available disk space and is faster.')
    parser.add_argument('--ignore-cache', action='store_true',
                        help='Ignore any previously cached files.')
    parser.add_argument('--build', metavar='build_version',
                        default='',
                        help='Specify a specific build to search for and '
                        'download.')
    parser.add_argument('--list', action='store_true',
                        help='Output the available updates to a plist '
                        'and quit.')
    parser.add_argument('--current', action='store_true',
                        help='Automatically select the current installed '
                        'build.')
    parser.add_argument('--validate', action='store_true',
                        help='Validate builds for board ID and hardware model '
                        'and only show appropriate builds.')
    parser.add_argument('--auto', action='store_true',
                        help='Automatically select the appropriate valid build '
                        'for the current device.')
    parser.add_argument('--version', metavar='match_version',
                        default='',
                        help='Selects the lowest valid build ID matching'
                        'the selected version.')
    args = parser.parse_args()

    # show this Mac's hardware model
    hw_model = get_hw_model()
    print "This Mac's ModelIdentifier:   %s" % hw_model
    # show this Mac's board-id
    board_id = get_board_id()
    print "This Mac's Board ID:          %s" % board_id
    os_build = get_current_build()
    print "This Mac's Current Build ID:  %s\n" % os_build

    # download sucatalog and look for products that are for macOS installers
    catalog = download_and_parse_sucatalog(
        args.catalogurl, args.workdir, ignore_cache=args.ignore_cache)
    product_info = os_installer_product_info(
        catalog, args.workdir, ignore_cache=args.ignore_cache)

    if not product_info:
        print >> sys.stderr, (
            'No macOS installer products found in the sucatalog.')
        exit(-1)

    output_plist = "%s/softwareupdate.plist" % args.workdir
    pl = {}
    pl['result'] = []

    # display a menu of choices (some seed catalogs have multiple installers)
    print '%2s  %-15s %-10s %-8s %-11s %-20s %s' % ('#', 'ProductID', 'Version',
                                     'Build', 'Post Date', 'Title', 'Notes')
    for index, product_id in enumerate(product_info):
        not_valid = ''
        if board_id not in product_info[product_id]['BoardIDs']:
            not_valid = 'Not valid on this device'
        if hw_model in product_info[product_id]['UnsupportedModels']:
            not_valid = 'Not valid on this device'
        print '%2s  %-15s %-10s %-8s %-11s %-20s %s' % (
            index + 1,
            product_id,
            product_info[product_id]['version'],
            product_info[product_id]['BUILD'],
            product_info[product_id]['PostDate'].strftime('%Y-%m-%d'),
            product_info[product_id]['title'],
            not_valid
        )

        # go through various options for automatically determining the answer

        # skip if build is not suitable for current device
        # and a validation parameter was chosen
        if not_valid and (args.validate or args.auto or args.version):
            continue

        # skip if a version is selected and it does not match
        if args.version and args.version != product_info[product_id]['version']:
            continue

        # determine the lowest valid build ID and select this
        # when using auto and version options
        if (args.auto or args.version) and 'Beta' not in product_info[product_id]['title']:
            try:
                lowest_valid_build
            except NameError:
                lowest_valid_build = product_info[product_id]['BUILD']
                answer = index+1
            else:
                lowest_valid_build = get_lowest_version(
                                        product_info[product_id]['BUILD'],
                                        lowest_valid_build)
                if lowest_valid_build == product_info[product_id]['BUILD']:
                    answer = index+1

        # Write this build info to plist
        pl_index =  {'index': index+1,
                'product_id': product_id,
                'version': product_info[product_id]['version'],
                'build': product_info[product_id]['BUILD'],
                'title': product_info[product_id]['title'],
                }
        pl['result'].append(pl_index)

        if args.build:
            # automatically select matching build ID if build option used
            if args.build == product_info[product_id]['BUILD']:
                answer = index+1
                break

        elif args.current:
            # automatically select matching build ID if current option used
            if os_build == product_info[product_id]['BUILD']:
                answer = index+1
                break

    # Output a plist of available updates and quit if list option chosen
    if args.list:
        plistlib.writePlist(pl, output_plist)
        exit(0)

    # check for validity of specified build if argument supplied
    if args.build:
        try:
            answer
        except NameError:
            print ('\n'
                   'Build %s is not available. '
                   'Run again without --build argument '
                   'to select a valid build to download.\n' % args.build)
            exit(0)
        else:
            print '\nBuild %s available. Downloading #%s...\n' % (args.build, answer)
    elif args.current:
        try:
            answer
        except NameError:
            print ('\n'
                   'Build %s is not available. '
                   'Run again without --current argument '
                   'to select a valid build to download.\n' % os_build)
            exit(0)
        else:
            print '\nBuild %s available. Downloading #%s...\n' % (os_build, answer)
    elif args.auto or args.version:
        try:
            answer
        except NameError:
            print ('\n'
                   'Item # %s is not available. '
                   'Run again without --auto argument '
                   'to select a valid build to download.\n' % answer)
            exit(0)
        else:
            print '\nBuild %s selected. Downloading #%s...\n' % (lowest_valid_build, answer)
    else:
        # default option to interactively offer selection
        answer = raw_input(
                '\nChoose a product to download (1-%s): ' % len(product_info))

    try:
        index = int(answer) - 1
        if index < 0:
            raise ValueError
        product_id = product_info.keys()[index]
    except (ValueError, IndexError):
        print 'Exiting.'
        exit(0)

    # download all the packages for the selected product
    replicate_product(
        catalog, product_id, args.workdir, ignore_cache=args.ignore_cache)

    # generate a name for the sparseimage
    volname = ('Install_macOS_%s-%s'
               % (product_info[product_id]['version'],
                  product_info[product_id]['BUILD']))
    sparse_diskimage_path = os.path.join(args.workdir, volname + '.sparseimage')
    if os.path.exists(sparse_diskimage_path):
        os.unlink(sparse_diskimage_path)

    # make an empty sparseimage and mount it
    print 'Making empty sparseimage...'
    sparse_diskimage_path = make_sparse_image(volname, sparse_diskimage_path)
    mountpoint = mountdmg(sparse_diskimage_path)
    if mountpoint:
        # install the product to the mounted sparseimage volume
        success = install_product(
            product_info[product_id]['DistributionPath'],
            mountpoint)
        if not success:
            print >> sys.stderr, 'Product installation failed.'
            unmountdmg(mountpoint)
            exit(-1)
        # add the seeding program xattr to the app if applicable
        seeding_program = get_seeding_program(args.catalogurl)
        if seeding_program:
            installer_app = find_installer_app(mountpoint)
            if installer_app:
                xattr.setxattr(installer_app, 'SeedProgram', seeding_program)
        print 'Product downloaded and installed to %s' % sparse_diskimage_path
        if args.raw:
            unmountdmg(mountpoint)
        else:
            # if --raw option not given, create a r/o compressed diskimage
            # containing the Install macOS app
            compressed_diskimagepath = os.path.join(
                args.workdir, volname + '.dmg')
            if os.path.exists(compressed_diskimagepath):
                os.unlink(compressed_diskimagepath)
            app_path = find_installer_app(mountpoint)
            if app_path:
                make_compressed_dmg(app_path, compressed_diskimagepath)
            # unmount sparseimage
            unmountdmg(mountpoint)
            # delete sparseimage since we don't need it any longer
            os.unlink(sparse_diskimage_path)


if __name__ == '__main__':
    main()
