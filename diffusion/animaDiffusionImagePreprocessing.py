#!/usr/bin/python
# Warning: works only on unix-like systems, not windows where "python animaDiffusionImagePreprocessing.py ..." has to be run

import sys
import argparse
import tempfile
import pydicom
import numpy as np
import struct

if sys.version_info[0] > 2:
    import configparser as ConfParser
else:
    import ConfigParser as ConfParser

import os
import shutil
from subprocess import call

configFilePath = os.path.expanduser("~") + "/.anima/config.txt"
if not os.path.exists(configFilePath):
    print('Please create a configuration file for Anima python scripts. Refer to the README')
    quit()

configParser = ConfParser.RawConfigParser()
configParser.read(configFilePath)

animaDir = configParser.get("anima-scripts", 'anima')
animaDataDir = configParser.get("anima-scripts", 'extra-data-root')
animaScriptsDir = configParser.get("anima-scripts", 'anima-scripts-root')

# Argument parsing
parser = argparse.ArgumentParser(
    description="Prepares DWI for model estimation: gradients reworking on Siemens based on dicoms, denoising, brain masking, distortion correction.")
parser.add_argument('-b', '--bval', type=str, required=True, help="DWI b-values file")
parser.add_argument('-g', '--grad', type=str, default="", help="DWI gradients file")
parser.add_argument('-r', '--reverse', type=str, default="", help="Reversed PED B0 image")
parser.add_argument('-d', '--direction', type=int, default=1, help="PED direction (0: x, 1: y, 2: z)")
parser.add_argument('-D', '--dicom', type=str, nargs='+', default="",
                    help="Dicom file to put dcm2nii bvec file to real coordinates")
parser.add_argument('--no-disto-correction', action='store_true', help="Do not perform distortion correction")
parser.add_argument('--no-denoising', action='store_true', help="Do not perform NL-Means denoising")
parser.add_argument('-t', '--t1', type=str, default="", help="T1 image for brain masking (B0 used if not provided)")
parser.add_argument('--no-brain-masking', action='store_true', help="Do not perform any brain masking")
parser.add_argument('--no-eddy-correction', action='store_true',
                    help="Do not perform Eddy current distortion correction")
parser.add_argument('-i', '--input', type=str, required=True, help='DWI file to process')

args = parser.parse_args()

tmpFolder = tempfile.mkdtemp()

dwiImage = args.input
dwiImagePrefix = os.path.splitext(dwiImage)[0]
if os.path.splitext(dwiImage)[1] == '.gz':
    dwiImagePrefix = os.path.splitext(dwiImagePrefix)[0]

tmpDWIImagePrefix = os.path.join(tmpFolder, os.path.basename(dwiImagePrefix))

outputImage = dwiImage
outputBVec = args.grad

if not (args.dicom == "") and not (args.grad == ""):
    # adapted from http://neurohut.blogspot.fr/2015/11/how-to-extract-bval-bvec-from-dicom.html
    # The goal here is to ensure the bvec file extracted from dcm2nii is well put
    # back in real coordinates. This assumes dcm2nii worked for gradient extraction which is not always the case.
    # If not, use the dicom folder option In any case, it works only for Siemens scanners though as far as I know
    image = pydicom.read_file(args.dicom[0])
    img_plane_position = image[0x0020, 0x0037].value
    V1 = np.array([float(img_plane_position[0]), float(img_plane_position[1]), float(img_plane_position[2])])
    V2 = np.array([float(img_plane_position[3]), float(img_plane_position[4]), float(img_plane_position[5])])
    V3 = np.cross(V1, V2)

    orMatrix = np.array([V1, V2, V3])

    bvecs = np.loadtxt(args.grad)
    bvecs_corrected = np.dot(orMatrix.transpose(), bvecs)

    np.savetxt(tmpDWIImagePrefix + "_real.bvec", bvecs_corrected, fmt="%.12f")
    outputBVec = tmpDWIImagePrefix + "_real.bvec"

elif not (args.dicom == "") and (args.grad == ""):
    bvecs_corrected = []
    acq_number = -1
    for dicom_file in sorted(args.dicom):
        image = pydicom.read_file(dicom_file)
        bval = float(image[0x0019, 0x100c].value)

        if image[0x0019, 0x100d].value == 'NONE' or bval == 0:
            bvec = [0, 0, 0]
        else:
            buffer = struct.unpack('ddd', image[0x0019, 0x100e].value)
            img_plane_position = image[0x0020, 0x0037].value
            vec = np.array(buffer)
            V1 = np.array([float(img_plane_position[0]), float(img_plane_position[1]), float(img_plane_position[2])])
            V2 = np.array([float(img_plane_position[3]), float(img_plane_position[4]), float(img_plane_position[5])])
            V3 = np.cross(V1, V2)

            bvec = np.zeros(3)
            bvec = vec

        add_bvec = True
        if acq_number == image[0x0020, 0x0012].value :
            add_bvec = False
        else:
            acq_number = image[0x0020, 0x0012].value

        if add_bvec:
            bvecs_corrected.append(bvec)

    bvecs_corrected = np.array(bvecs_corrected)
    np.savetxt(tmpDWIImagePrefix + "_real.bvec", bvecs_corrected.transpose(), fmt="%.12f")
    outputBVec = tmpDWIImagePrefix + "_real.bvec"

if outputBVec == "":
    sys.exit("Gradient file needs to be provided (either through Dicom folder or through dcm2nii)")

# Distortion correction first
# Eddy current first
if args.no_eddy_correction is False:
    eddyCorrectionCommand = [animaDir + "animaEddyCurrentCorrection", "-i", dwiImage, "-I", outputBVec, "-o",
                             tmpDWIImagePrefix + "_eddy_corrected.nrrd", \
                             "-O", tmpDWIImagePrefix + "_eddy_corrected.bvec", "-d", str(args.direction)]
    call(eddyCorrectionCommand)

    outputImage = tmpDWIImagePrefix + "_eddy_corrected.nrrd"
    outputBVec = tmpDWIImagePrefix + "_eddy_corrected.bvec"

# Extract brain from T1 image if present (used for further processing)
if (args.no_disto_correction is False or args.no_brain_masking is False) and not args.t1 == "":
    brainExtractionCommand = ["python", animaScriptsDir + "brain_extraction/animaAtlasBasedBrainExtraction.py", args.t1]
    call(brainExtractionCommand)

# Then susceptibility distortion
if args.no_disto_correction is False:
    if not (args.reverse == ""):
        b0ExtractCommand = [animaDir + "animaCropImage", "-i", outputImage, "-t", "0", "-T", "0", "-o",
                            tmpDWIImagePrefix + "_B0.nrrd"]
        call(b0ExtractCommand)

        idTrsfName = os.path.join(animaDataDir, "id.txt")
        idTrsfXmlName = os.path.join(tmpFolder, "id.xml")
        idGenCommand = [animaDir + "animaTransformSerieXmlGenerator", "-i", idTrsfName, "-o", idTrsfXmlName]
        call(idGenCommand)

        resampleB0PACommand = [animaDir + "animaApplyTransformSerie", "-i", args.reverse, "-t", idTrsfXmlName, "-o",
                               tmpDWIImagePrefix + "_B0_Reverse.nrrd", "-g", tmpDWIImagePrefix + "_B0.nrrd"]
        call(resampleB0PACommand)

        initCorrectionCommand = [animaDir + "animaDistortionCorrection", "-s", "2", "-d", str(args.direction), \
                                 "-f", tmpDWIImagePrefix + "_B0.nrrd", "-b", tmpDWIImagePrefix + "_B0_Reverse.nrrd",
                                 "-o", tmpDWIImagePrefix + "_init_correction_tr.nrrd"]
        call(initCorrectionCommand)
        bmCorrectionCommand = [animaDir + "animaBMDistortionCorrection", "-f", tmpDWIImagePrefix + "_B0.nrrd", \
                               "-b", tmpDWIImagePrefix + "_B0_Reverse.nrrd", "-o",
                               tmpDWIImagePrefix + "_B0_corrected.nrrd", "-i",
                               tmpDWIImagePrefix + "_init_correction_tr.nrrd", \
                               "--bs", "3", "-s", "10", "-d", str(args.direction), "-O",
                               tmpDWIImagePrefix + "_B0_correction_tr.nrrd"]
        call(bmCorrectionCommand)

        applyCorrectionCommand = [animaDir + "animaApplyDistortionCorrection", "-f", outputImage, "-t", \
                                  tmpDWIImagePrefix + "_B0_correction_tr.nrrd", "-o",
                                  tmpDWIImagePrefix + "_corrected.nrrd"]
        call(applyCorrectionCommand)

        outputImage = tmpDWIImagePrefix + "_corrected.nrrd"
    elif not (args.t1 == ""):
        b0ExtractCommand = [animaDir + "animaCropImage", "-i", outputImage, "-t", "0", "-T", "0", "-o",
                            tmpDWIImagePrefix + "_B0.nrrd"]
        call(b0ExtractCommand)

        T1Prefix = os.path.splitext(args.t1)[0]
        if os.path.splitext(args.t1)[1] == '.gz':
            T1Prefix = os.path.splitext(T1Prefix)[0]

        tmpT1Prefix = os.path.join(tmpFolder, os.path.basename(T1Prefix))

        correctionCommand = [animaDir + "animaPyramidalBMRegistration", "-r", tmpDWIImagePrefix + "_B0.nrrd", \
                             "-m", T1Prefix + "_masked.nrrd", "-o", tmpT1Prefix + "_rig.nrrd"]
        call(correctionCommand)

        correctionCommand = [animaDir + "animaDenseSVFBMRegistration", "-r", tmpT1Prefix + "_rig.nrrd", \
                             "-m", tmpDWIImagePrefix + "_B0.nrrd", "-o", tmpDWIImagePrefix + "_B0_corrected.nrrd", "-d",
                             str(args.direction), \
                             "-O", tmpDWIImagePrefix + "_B0_correction_tr.nrrd", "-t", "3"]
        call(correctionCommand)

        applyCorrectionCommand = [animaDir + "animaApplyDistortionCorrection", "-f", outputImage, "-t", \
                                  tmpDWIImagePrefix + "_B0_correction_tr.nrrd", "-o",
                                  tmpDWIImagePrefix + "_corrected.nrrd"]
        call(applyCorrectionCommand)

        outputImage = tmpDWIImagePrefix + "_corrected.nrrd"

# Then re-orient image to be axial first
dwiReorientCommand = [animaDir + "animaConvertImage", "-i", outputImage, "-o", tmpDWIImagePrefix + "_or.nrrd", "-R",
                      "AXIAL"]
call(dwiReorientCommand)
outputImage = tmpDWIImagePrefix + "_or.nrrd"

# Then perform denoising
if args.no_denoising is False:
    denoisingCommand = [animaDir + "animaNLMeansTemporal", "-i", outputImage, "-b", "0.5", "-o",
                        tmpDWIImagePrefix + "_nlm.nrrd"]
    call(denoisingCommand)
    outputImage = tmpDWIImagePrefix + "_nlm.nrrd"

# Finally, brain mask image
if args.no_brain_masking is False:
    brainImage = args.t1

    b0ExtractCommand = [animaDir + "animaCropImage", "-i", outputImage, "-t", "0", "-T", "0", "-o",
                        tmpDWIImagePrefix + "_forBrainExtract.nrrd"]
    call(b0ExtractCommand)

    if brainImage == "":
        brainImage = tmpDWIImagePrefix + "_forBrainExtract.nrrd"
        brainExtractionCommand = ["python", animaScriptsDir + "brain_extraction/animaAtlasBasedBrainExtraction.py",
                                  brainImage]
        call(brainExtractionCommand)

    if args.t1 == "":
        shutil.move(tmpDWIImagePrefix + "_forBrainExtract_brainMask.nrrd", dwiImagePrefix + "_brainMask.nrrd")
    else:
        T1Prefix = os.path.splitext(args.t1)[0]
        if os.path.splitext(args.t1)[1] == '.gz':
            T1Prefix = os.path.splitext(T1Prefix)[0]

        tmpT1Prefix = os.path.join(tmpFolder, os.path.basename(T1Prefix))

        t1RegistrationCommand = [animaDir + "animaPyramidalBMRegistration", "-r",
                                 tmpDWIImagePrefix + "_forBrainExtract.nrrd", "-m", T1Prefix + "_masked.nrrd", "-o",
                                 tmpT1Prefix + "_rig.nrrd", "-O", tmpT1Prefix + "_rig_tr.txt", "-p", "4", "-l", "1",
                                 "--sp", "2", "-I", "0"]
        call(t1RegistrationCommand)

        command = [animaDir + "animaTransformSerieXmlGenerator", "-i", tmpT1Prefix + "_rig_tr.txt", "-o",
                   tmpT1Prefix + "_rig_tr.xml"]
        call(command)

        command = [animaDir + "animaApplyTransformSerie", "-i", T1Prefix + "_brainMask.nrrd", "-t",
                   tmpT1Prefix + "_rig_tr.xml", "-o", dwiImagePrefix + "_brainMask.nrrd", "-g",
                   tmpDWIImagePrefix + "_forBrainExtract.nrrd", "-n", "nearest"]
        call(command)

    brainExtractionCommand = [animaDir + "animaMaskImage", "-i", outputImage, "-m", dwiImagePrefix + "_brainMask.nrrd", \
                              "-o", tmpDWIImagePrefix + "_masked.nrrd"]
    call(brainExtractionCommand)

    outputImage = tmpDWIImagePrefix + "_masked.nrrd"

shutil.copy(outputImage, dwiImagePrefix + "_preprocessed.nrrd")
shutil.copy(outputBVec, dwiImagePrefix + "_preprocessed.bvec")

# Estimate tensors if files were provided
dtiEstimationCommand = [animaDir + "animaDTIEstimator", "-i", outputImage, "-o", dwiImagePrefix + "_Tensors.nrrd", \
                        "-O", dwiImagePrefix + "_Tensors_B0.nrrd", "-N", dwiImagePrefix + "_Tensors_NoiseVariance.nrrd", \
                        "-g", outputBVec, "-b", args.bval]

if args.no_brain_masking is False:
    dtiEstimationCommand += ["-m", dwiImagePrefix + "_brainMask.nrrd"]

call(dtiEstimationCommand)

shutil.rmtree(tmpFolder)
