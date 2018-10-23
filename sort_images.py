# Copyright 2018 The Fuego Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""

The inputs are zip file, fire ID, camera ID, timestamp of minimal smoke, timestamp of significant enough smoke for cropping.
The zip file should include at least one picture from before minimal smoke and most pictures between minimal smoke and significant smoke, and some number of images after as well

This script will unzip the images, update the image metadata sheet, and upload the images to google drive

"""

import zipfile
import tempfile
import os
import sys
import pathlib
import datetime
import dateutil.parser
import time
import re

from googleapiclient.discovery import build
from httplib2 import Http
from oauth2client import file, client, tools
from apiclient.http import MediaFileUpload

import settings
sys.path.insert(0, settings.fuegoRoot + '/lib')
import collect_args

import crop_single

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]
IMG_CLASSES = {
    'smoke': settings.smokePictures,
    'nonSmoke': settings.nonSmokePictures,
    'motion': settings.motionPictures,
    'cropSmoke': settings.cropSmokePictures
}


def getGoogleServices(args):
    store = file.Storage(settings.googleTokenFile)
    creds = store.get()
    if not creds or creds.invalid:
        flow = client.flow_from_clientsecrets(settings.googleCredsFile, ' '.join(SCOPES))
        creds = tools.run_flow(flow, store, args)
    driveService = build('drive', 'v3', http=creds.authorize(Http()))
    sheetService = build('sheets', 'v4', http=creds.authorize(Http()))
    return {
        'drive': driveService,
        'sheet': sheetService
    }


def driveListFiles(service, parentID, searchName=None):
    page_token = None
    param = {}
    param['q'] = "'" + parentID + "' in parents and trashed = False"
    if (searchName != None):
        param['q'] = param['q'] + "and name = '" + searchName + "'"
    param['fields'] = 'nextPageToken, files(id, name)'
    param['pageToken'] = page_token
    param['supportsTeamDrives'] = True
    param['includeTeamDriveItems'] = True
    # print(param)
    results = service.files().list(**param).execute()
    items = results.get('files', [])
    # print('Files: ', items)
    return items


def uploadToDrive(service, imgPath, cameraID, imgClass):
    # page_token = None
    # param = {}
    # param['fields'] = 'nextPageToken, teamDrives(id, name)'
    # param['pageToken'] = page_token
    # response = service.teamdrives().list(**param).execute()
    # print('param resp', response)

    # print('top')
    # driveListFiles(service, settings.teamDriveID)
    # print('pics')
    # driveListFiles(service, settings.allPictures)
    # print('smoke')
    # driveListFiles(service, settings.smokePictures, cameraID)
    # print('non')
    # driveListFiles(service, settings.nonSmokePictures)
    # print('motion')
    # driveListFiles(service, settings.motionPictures)

    parent = IMG_CLASSES[imgClass]
    dirName = ''
    dirID = parent
    if cameraID != None:
        dirs = driveListFiles(service, parent, cameraID)
        if len(dirs) != 1:
            print('Expected 1 directory but found', len(dirs), dirs)
            exit(1)
        dirID = dirs[0]['id']
        dirName = dirs[0]['name']

    file_metadata = {'name': pathlib.PurePath(imgPath).name, 'parents': [dirID]}
    media = MediaFileUpload(imgPath,
                            mimetype='image/jpeg')
    file2 = service.files().create(body=file_metadata,
                                        media_body=media,
                                        supportsTeamDrives=True,
                                        fields='id').execute()
    print('Uploaded file ', imgPath, ' to ', imgClass, dirName)


def getTimeFromName(imgName):
    # regex to match names like Axis-BaldCA_2018-05-29T16_02_30_129496.jpg
    # and bm-n-mobo-c__2017-06-25z11;53;33.jpg
    regexExpanded = '([A-Za-z0-9-]+)_*(\d{4}-\d\d-\d\d)T(\d\d)[_;](\d\d)[_;](\d\d)'
    # regex to match names like 1499546263.jpg
    regexUnixTime = '1\d{9}'
    matchesExp = re.findall(regexExpanded, imgName)
    matchesUnix = re.findall(regexUnixTime, imgName)
    if len(matchesExp) == 1:
        isoStr = '{date}T{hour}:{min}:{sec}'.format(date=matchesExp[0][1],hour=matchesExp[0][2],min=matchesExp[0][3],sec=matchesExp[0][4])
        dt = dateutil.parser.parse(isoStr)
        unixTime = time.mktime(dt.timetuple())
    elif len(matchesUnix) == 1:
        unixTime = int(matchesUnix[0])
        isoStr = datetime.datetime.fromtimestamp(unixTime).isoformat()
    else:
        print('Failed to parse image name', imgName)
        barf()
    return {
        'unixTime': unixTime,
        'isoStr': isoStr
    }


def renameToIso(dirName, imgName, times, cameraId):
    isoTime = times['isoStr'].replace(':', ';') # make windows happy
    oldFullPath = os.path.join(dirName, imgName)
    imgExtension = os.path.splitext(imgName)[1]
    newName = cameraId + '__' + isoTime + imgExtension
    newFullPath = os.path.join(dirName, newName)
    print(oldFullPath, newFullPath)
    os.rename(oldFullPath, newFullPath)
    return newFullPath


def getClass(times, initialTime, enoughTime):
    unixTime = times['unixTime']
    initialTime = int(initialTime)
    enoughTime = int(enoughTime)
    # print(unixTime, initialTime, enoughTime)
    if unixTime < initialTime:
        return 'nonSmoke'
    elif unixTime < enoughTime:
        return 'motion'
    else:
        return 'smoke'


def appendToMainSheet(service, imgPath, times, cameraID, imgClass, fireID):
    # result = service.spreadsheets().values().get(spreadsheetId=settings.imagesSheet,
    #                                             range=settings.imagesSheetAppendRange).execute()
    # print(result)
    # values = result.get('values', [])
    # print(values)

    imgName = pathlib.PurePath(imgPath).name
    timeStr = datetime.datetime.fromtimestamp(times['unixTime']).strftime('%F %T')

    value_input_option="USER_ENTERED" # vs "RAW"
    values = [[
        imgName,
        imgClass,
        fireID,
        cameraID,
        timeStr, #time
        "yes" if imgClass == 'smoke' else "no", #smoke boolean
        "no", #fog boolean
        "no", #rain boolean
        "no", #glare boolean
        "no" #snow boolean
        ]]
    body = {
        'values': values
    }
    result = service.spreadsheets().values().append(
        spreadsheetId=settings.imagesSheet, range=settings.imagesSheetAppendRange,
        valueInputOption=value_input_option, body=body).execute()
    print('{0} cells updated.'.format(result.get('updatedCells')))


def appendToCropSheet(service, cropPath, coords, basePath):
    cropName = pathlib.PurePath(cropPath).name
    baseName = pathlib.PurePath(basePath).name
    value_input_option="USER_ENTERED" # vs "RAW"
    values = [[
        cropName,
        coords[0],
        coords[1],
        coords[2],
        coords[3],
        baseName
        ]]
    body = {
        'values': values
    }
    result = service.spreadsheets().values().append(
        spreadsheetId=settings.cropImagesSheet, range=settings.cropImagesSheetAppendRange,
        valueInputOption=value_input_option, body=body).execute()
    print('{0} cells updated.'.format(result.get('updatedCells')))


def unzipFile(args, googleServices):
    tempDir = tempfile.TemporaryDirectory()
    print('tempDir', tempDir.name)
    with zipfile.ZipFile(args.zipFile, "r") as zip_ref:
        zip_ref.extractall(tempDir.name)
        imageFileNames = os.listdir(tempDir.name)
        # print('images', imageFileNames)
        # we want to process in time order, so first create tuples with associated time
        tuples=list(map(lambda x: (x,getTimeFromName(x)['unixTime']), imageFileNames))
        lastSmokeTimestamp=None
        for tuple in sorted(tuples, key=lambda x: x[1]):
            imgName=tuple[0]
            times = getTimeFromName(imgName)
            newPath = renameToIso(tempDir.name, imgName, times, args.camera)
            imgClass = getClass(times, args.initialTime, args.enoughTime)
            print(imgClass, newPath)
            uploadToDrive(googleServices['drive'], newPath, args.camera, imgClass)
            appendToMainSheet(googleServices['sheet'], newPath, times, args.camera, imgClass, args.fire)
            if (imgClass == 'smoke'):
                if (lastSmokeTimestamp == None) or (times['unixTime'] - lastSmokeTimestamp >= settings.cropEveryNMinutes * 60):
                    lastSmokeTimestamp = times['unixTime']
                    result = crop_single.imageDisplay(newPath, settings.localCropDir)
                    if len(result) > 0:
                        for entry in result:
                            print('crop data', entry['name'], entry['coords'])
                            uploadToDrive(googleServices['drive'], entry['name'], None, 'cropSmoke')
                            appendToCropSheet(googleServices['sheet'], entry['name'], entry['coords'], newPath)

        imageFileNames = os.listdir(tempDir.name)
        print('images2', imageFileNames)


def main():
    allArgs = [
        ["z", "zipFile", "Name of the zip file containing the images"],
        ["f", "fire", "ID of the fire in the images"],
        ["c", "camera", "ID of the camera used in the images"],
        ["i", "initialTime", "Time of initial image with smoke"],
        ["e", "enoughTime", "Time of first image with enough smoke"],
    ]
    args = collect_args.collectArgs(allArgs, parentParsers=[tools.argparser])
    if not os.path.isfile(args.zipFile):
        print('Zip file not found', args.zipFile)
        exit()

    googleServices = getGoogleServices(args)
    unzipFile(args, googleServices)

if __name__=="__main__":
    main()
