from src.constants.http_status_codes import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND
from flask import Blueprint, request, jsonify
import numpy as np
from PIL import Image
from src.services.model import extract_img
from flask_jwt_extended import jwt_required, get_jwt_identity
import cv2
import torch
import base64
import tempfile
import io
import threading
import uuid
from src.services.room import findById
from src.services.account import findByEmail
from src.controllers.room import getGoogleDrive

detect = Blueprint("detect", __name__, url_prefix="/api/v1/detect")

# # Download data.yaml file
# url = "https://raw.githubusercontent.com/ultralytics/yolov5/master/data/coco.yaml"
# filename, _ = urllib.request.urlretrieve(url, filename="./data.yaml")

torch.hub._validate_not_a_forked_repo=lambda a,b,c: True
modelSystem = torch.hub.load('ultralytics/yolov5', 'custom', path='src/models/system/yolov5s.pt', verbose=False)
modelSystem.eval()

# Define a function for uploading a single file
def upload_file_image(drive, file, folderId):
    file_name = file.filename
    gfile = drive.CreateFile({'parents': [{'id': folderId, 'title': file_name}]})
    
    # Save file to temporary directory
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        file.save(tmp.name)
        file_path_image = tmp.name
    
    # Set content of file
    gfile.SetContentFile(file_path_image)
    gfile.Upload() # Upload the file.
    gfile.InsertPermission({
        'type': 'anyone',
        'value': 'anyone',
        'role': 'reader',
    })
        # Set public access and download URL
    gfile['alternateLink'] = None  # Set to None to disable webContentLink
    gfile['shared'] = True
    
    gfile['title'] = file_name
    gfile.Upload()

def upload_file_text(drive, file, folderId):
    file_name = file.filename
    gfile = drive.CreateFile({'parents': [{'id': folderId, 'title': file_name}]})
    
    # Set content of file
    gfile.SetContentString(file.read().decode("utf-8"))
    gfile.Upload() # Upload the file.
    gfile.InsertPermission({
        'type': 'anyone',
        'value': 'anyone',
        'role': 'reader',
    })
        # Set public access and download URL
    gfile['alternateLink'] = None  # Set to None to disable webContentLink
    gfile['shared'] = True
    
    gfile['title'] = file_name
    gfile.Upload()

def processFileName(file):
    split_name = file.filename.split('.')
    type_file = split_name[-1]
    file_name = '.'.join(split_name[:-1])
    return {
        'typeFile': type_file,
        'fileName': file_name
    }

@detect.post("/system-model")
def detectSystemModel():
    file = extract_img(request)
    # Load the image
    img = Image.open(file).convert('RGB')

    # Convert the image to a numpy array
    img_array = np.array(img)

    # Use YOLOv5 to detect objects in the image
    results = modelSystem(img_array)

    # Extract the bounding boxes, class labels, and confidence scores from the results
    boxes = results.xyxy[0].numpy().tolist()
    class_ids = results.pred[0].numpy()[:, 5].tolist() # extract class ids
    labels = [results.names[int(class_id)] for class_id in class_ids] # convert class ids to labels
    scores = results.pred[0].numpy()[:, 4].tolist() # extract confidence scores

    # Loop over the bounding boxes and draw them on the image
    detections = []
    for i, box in enumerate(boxes):
        x1, y1, x2, y2, score, class_id = box
        label = results.names[int(class_id)]
        conf = f"{score:.2f}"
        cropped_img = img.crop((x1, y1, x2, y2))
        # Convert the image to a base64-encoded string
        output = io.BytesIO()
        cropped_img.save(output, format='JPEG')
        output.seek(0)
        result_bytes_crop = output.getvalue()
        result_str_crop = base64.b64encode(result_bytes_crop).decode('utf-8')
        # cropped_img_base64 = base64.b64encode(cropped_img.tobytes()).decode('utf-8')
        detections.append({'label': label, 'confidence': conf, 'image': result_str_crop})
    results.render()
    # encoding the resulting image and return it
    for img in results.ims:
        RGB_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        im_arr = cv2.imencode('.jpg', RGB_img)[1]
        result_bytes = im_arr.tobytes()
        # response = make_response(im_arr.tobytes())
    
    # Encode the bytes using base64
    result_str = base64.b64encode(result_bytes).decode('utf-8')

    return jsonify({'result': result_str, 'detections': detections})

@detect.post("/upload-data")
@jwt_required()
def uploadDataSet():
    if 'image' not in request.files:
        return jsonify({
            'message': "Missing file image parameter!",
            'status': HTTP_400_BAD_REQUEST
        })
    if 'label' not in request.files:
        return jsonify({
            'message': "Missing file label parameter!",
            'status': HTTP_400_BAD_REQUEST
        })
    id = request.form['id']
    room = findById(id)
    if room is None:
            return jsonify({"error": "Not found room"}), HTTP_404_NOT_FOUND

    folderImageId = room['imageId']
    folderLabelId = room['labelId']
    email = room['email']

    newName = str(uuid.uuid4())
    image = request.files['image']
    processedFileImage = processFileName(image)
    type_file_image = processedFileImage['typeFile']
    image.filename = newName + "." + str(type_file_image)

    label = request.files['label']
    processedFileLabel = processFileName(label)
    type_file_label = processedFileLabel['typeFile']
    label.filename = newName + "." + str(type_file_label)

    userAdmin = findByEmail(email=email)
    if userAdmin is None:
        return jsonify({
            'error': 'User is not found!'
        }), HTTP_404_NOT_FOUND
    
    credentialsJs = userAdmin['credentials']
    if credentialsJs is None:
        return jsonify({
            'error': 'Created User doesnt connect drive!'
        }), HTTP_400_BAD_REQUEST
    drive = getGoogleDrive(credentialsJs=credentialsJs)
    # Upload file image
    upload_file_image(drive=drive, file=image, folderId=folderImageId)

    # Upload file label
    upload_file_text(drive=drive, file=label, folderId=folderLabelId)
    
    return jsonify({
        'status': 'ok'
    })

@detect.post("/upload-multi-data")
@jwt_required()
def uploadMultiDataSet():
    if 'images' not in request.files:
        return jsonify({
            'message': "Missing file image parameter!",
            'status': HTTP_400_BAD_REQUEST
        })
    if 'labels' not in request.files:
        return jsonify({
            'message': "Missing file label parameter!",
            'status': HTTP_400_BAD_REQUEST
        })
    id = request.form['id']
    room = findById(id)
    if room is None:
            return jsonify({"error": "Not found room"}), HTTP_404_NOT_FOUND

    folderImageId = room['imageId']
    folderLabelId = room['labelId']
    email = room['email']
    images = request.files.getlist('images')
    labels = request.files.getlist('labels')

    userAdmin = findByEmail(email=email)
    if userAdmin is None:
        return jsonify({
            'error': 'User is not found!'
        }), HTTP_404_NOT_FOUND
    
    credentialsJs = userAdmin['credentials']
    if credentialsJs is None:
        return jsonify({
            'error': 'Created User doesnt connect drive!'
        }), HTTP_400_BAD_REQUEST
    drive = getGoogleDrive(credentialsJs=credentialsJs)
    
    mapUuid = {}
    for image in images:
        processedFile = processFileName(image)
        file_name = processedFile['fileName']
        type_file = processedFile['typeFile']
        newName = str(uuid.uuid4())
        mapUuid[file_name] = newName
        image.filename = newName + "." + str(type_file)
        upload_file_image(drive=drive, file=image, folderId=folderImageId)

    for label in labels:
        # Upload file label
        processedFile = processFileName(label)
        file_name = processedFile['fileName']
        type_file = processedFile['typeFile']
        newName = mapUuid.get(file_name)
        label.filename = newName + "." + str(type_file)
        upload_file_text(drive=drive, file=label, folderId=folderLabelId)
    
    return jsonify({
        'status': 'ok'
    })

@detect.get("/list-files")
@jwt_required()
def getListFilesByFolderId():
    email = get_jwt_identity()
    folderId = request.args.get('folderId')

    userAdmin = findByEmail(email=email)
    if userAdmin is None:
        return jsonify({
            'error': 'User is not found!'
        }), HTTP_404_NOT_FOUND
    credentialsJs = userAdmin['credentials']
    if credentialsJs is None:
        return jsonify({
            'error': 'Created User doesnt connect drive!'
        }), HTTP_400_BAD_REQUEST
    drive = getGoogleDrive(credentialsJs=credentialsJs)
    file_list = drive.ListFile({'q': "'{}' in parents and trashed=false".format(folderId)}).GetList()
    file_list_convert = []
    for file in file_list:
        file_list_convert.append({
            'id': file['id'],
            'title': file['title'], 
            'thumnailLink': file['thumbnailLink'],
            'downloadLink': file['webContentLink']
        })
    
    return jsonify({
        'listFiles': file_list_convert
    })