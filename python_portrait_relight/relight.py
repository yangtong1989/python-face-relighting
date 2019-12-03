# -*- coding: utf-8 -*-
""" python implementation of Face relighting.

Paper: 
    portrait lighting transfer using a mass transport approach. (2018)
Author's Matlab Implementation:
    https://github.com/AjayNandoriya/PortraitLightingTransferMTP
"""
import os
import sys

import cv2
import numpy as np
from face3d.face3d.mesh.render import render_colors as render_texture


# PRNet, pytorch_retinaface use relative import
CUR_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(CUR_DIR, 'PRNet'))
sys.path.insert(0, os.path.join(CUR_DIR, 'pytorch_retinaface'))

from .src.utils import frontalize, normalize_v3
from .python_color_transfer.color_transfer import ColorTransfer, Regrain
from .PRNet.api import PRN

class Relight:
    """Methods for relighting human faces."""
    def __init__(self, m=20, c=8, smoothness=0.125):
        pr_folder = os.path.join(CUR_DIR, 'PRNet')
        self.prn = PRN(is_dlib=True, prefix=pr_folder) 
        self.ct = ColorTransfer(m=m, c=c)
        self.rg = Regrain(smoothness=smoothness)
        self.mask1d = self.prn.face_ind
        self.triangles = self.prn.triangles
    def get_normals(self, vertices=None):
        '''get normals of vertices. 
        
        Args:
            vertices: coordinates of vertices, shape=(n, 3)
            faces: faces represented by vertice ind triplet, shape=(m, 3)
        Returns:
            normals: normal vectors of vertices, shape=(n, 3)
        '''
        # Create a zeroed array with the same type and shape as our vertices i.e., per vertex normal
        normals = np.zeros(vertices.shape, dtype=vertices.dtype )
        # Create an indexed view into the vertex array using the array of three indices for triangles
        tris = vertices[self.triangles]
        # Calculate the normal for all the triangles, by taking the cross product of the vectors v1-v0, 
        # and v2-v0 in each triangle             
        n = np.cross(tris[::,1]-tris[::,0], tris[::,2]-tris[::,0])
        # n is now an array of normals per triangle. The length of each normal is dependent the vertices, 
        # we need to normalize these, so that our next step weights each normal equally.
        normalize_v3(n)
        # now we have a normalized array of normals, one per triangle, i.e., per triangle normals.
        # But instead of one per triangle (i.e., flat shading), we add to each vertex in that triangle, 
        # the triangles' normal. Multiple triangles would then contribute to every vertex, so we need to normalize again afterwards.
        # The cool part, we can actually add the normals through an indexed view of our (zeroed) per vertex normal array
        normals[self.triangles[:,0]] += n
        normals[self.triangles[:,1]] += n
        normals[self.triangles[:,2]] += n
        normalize_v3(normals)
        return normals
    def get_pos(self, img_arr=None, img_info=None):
        '''get 3d position map by PRNet.
        
        Args:
            img_arr: face img to extract 3d face position map.
            img_info: [x1, x2, y1, y2]
        '''
        pos = self.prn.process(img_arr, image_info=img_info)
        return pos
    def regrain(self, img_arr_in=None, img_arr_col=None):
        '''Regrain algorithm implemented in python_color_transfer.'''
        img_arr_out = self.rg.regrain(img_arr_in=img_arr_in,
                                      img_arr_col=img_arr_col)
        return img_arr_out
    def relight_features(self, features=None, ref_features=None):
        '''Relight by applying pdf transfer.'''
        features = features.transpose()
        ref_features = ref_features.transpose()
        out_features = self.ct.pdf_transfer_nd(arr_in=features, 
                                               arr_ref=ref_features,
                                               step_size=0.2)
        out_features = out_features.transpose()
        return out_features
    def relight(self, img_arr=None, ref_arr=None,
                box=None, ref_box=None):
        '''Relight img_arr according to ref_arr.
        
        Args:
            img_arr: input bgr numpy array.
            ref_arr: reference bgr numpy array, target lighting condition.
            box: face box of img_arr.
            ref_box: face box of ref_arr.
        Returns:
            out_arr: relighted bgr array of img_arr.
        '''
        # get 3d positions by PRNet
        [x1, y1, x2, y2] = box
        img_info = np.array([x1, x2, y1, y2]) 
        pos = self.get_pos(img_arr=img_arr, img_info=img_info)
        [x1, y1, x2, y2] = ref_box
        img_info = np.array([x1, x2, y1, y2])
        ref_pos = self.get_pos(img_arr=ref_arr, img_info=img_info)
        # obtain texture by remapping
        texture = cv2.remap(img_arr, pos[:,:,:2].astype(np.float32), 
                            None, interpolation=cv2.INTER_NEAREST, 
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0))
        ref_texture = cv2.remap(ref_arr, ref_pos[:,:,:2].astype(np.float32), 
                                None, interpolation=cv2.INTER_NEAREST, 
                                borderMode=cv2.BORDER_CONSTANT, borderValue=(0))
        # colors
        colors = texture.reshape(-1, 3).astype(np.float64)[self.mask1d, :]
        ref_colors = ref_texture.reshape(-1, 3).astype(np.float64)[self.mask1d, :]
        # vertices
        vertices = pos.reshape(-1, 3)[self.mask1d, :]
        ref_vertices = ref_pos.reshape(-1, 3)[self.mask1d, :]
        # frontalize
        vertices_f = frontalize(vertices)
        ref_vertices_f = frontalize(ref_vertices)
        # normals
        normals = self.get_normals(vertices=vertices_f)
        ref_normals = self.get_normals(vertices=ref_vertices_f)
        # normalize 
        colors_n = colors / 255.
        ref_colors_n = ref_colors / 255.
        vertices_n = vertices_f - vertices_f.min()
        vertices_n = vertices_n / (vertices_n.max() + 1e-6)
        ref_vertices_n = ref_vertices_f - ref_vertices_f.min()
        ref_vertices_n = ref_vertices_n / (ref_vertices_n.max() + 1e-6)
        # concatenate
        features = np.concatenate((colors_n, vertices_n[:,:2], normals), axis=1)
        ref_features = np.concatenate((ref_colors_n, ref_vertices_n[:,:2], ref_normals), axis=1)
        # relighting by color transfer
        out_features = self.relight_features(features=features, ref_features=ref_features)
        out_colors = out_features[:, :3]
        out_colors[out_colors < 0] = 0
        out_colors[out_colors > 1] = 1
        out_colors = (out_colors*255).astype('uint8')
        # render
        [height, width, _] = img_arr.shape
        out_arr = render_texture(vertices=vertices.T, colors=out_colors.T, 
                                 triangles=self.triangles.T, h=height, w=width, c=3)
        # mask
        visible_colors = np.ones((vertices.shape[0], 1))
        face_mask = render_texture(vertices=vertices.T, colors=visible_colors.T, 
                                   triangles=self.triangles.T, h=height, w=width, c=1)
        face_mask = np.squeeze(face_mask > 0).astype(np.float32)
        # replace
        out_arr = img_arr*(1 - face_mask[:,:,np.newaxis]) + out_arr*face_mask[:,:,np.newaxis]
        # regrain
        out_arr = self.regrain(img_arr_in=img_arr, img_arr_col=out_arr)
        return out_arr
        


