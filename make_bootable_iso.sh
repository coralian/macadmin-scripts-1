#!/usr/bin/env bash
# download MacOS dmg from apple server and convert to a proper disk format
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

IDENTIFIER="com.mac.CreateBootableISO"
VERSION=1.0

if [ -z "$1" ]
  then
    os_ver="12.2.1"
else
    os_ver=$1
fi

if [ -z "$2" ]
  then
    os_name="Monterey"
else
    os_name=$2
fi

output_name="Install_macOS_$os_name_$os_ver"
mkdir -p ~/macOS-installer
cd ~/macOS-installer

if test -f "install_macos.py"; then
    echo "install_macos.py already exists, using local version"
  else
    echo "install_macos.py missing, pulling from git"
    curl https://raw.githubusercontent.com/coralian/macadmin-scripts/main/installinstallmacos.py > install_macos.py
fi

python install_macos.py --raw --version $os_ver --file_name $output_name

Echo "Creating & attaching temp disk image files"
hdiutil create -o "$output_name.cdr" -size 16g -layout SPUD -fs HFS+J
hdiutil attach "$output_name.cdr.dmg" -noverify -mountpoint /Volumes/install_build
hdiutil attach "$output_name.sparseimage" 

Echo "Create bootable install media on temp disk image"
/Volumes/$output_name/Applications/Install\ macOS\ $os_name.app/Contents/Resources/createinstallmedia --volume /Volumes/install_build --nointeraction
hdiutil detach "/Volumes/Install macOS $os_name" -force
hdiutil detach /Volumes/$output_name -force
hdiutil convert $output_name.cdr.dmg -format UDTO -o $output_name.iso
mv $output_name.iso.cdr $output_name.iso

Echo "Cleaning up...."
rm $output_name.cdr.dmg
rm $output_name.sparseimage

Echo "Bootable ISO output to ~/macOS-installer/$output_name.iso"
