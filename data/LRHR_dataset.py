import os.path
import random
import numpy as np
import cv2
import torch
import torch.utils.data as data
import data.util as util


class LRHRDataset(data.Dataset):
    '''
    Read LR and HR image pairs.
    If only HR image is provided, generate LR image on-the-fly.
    The pair is ensured by 'sorted' function, so please check the name convention.
    '''

    def __init__(self, opt):
        super(LRHRDataset, self).__init__()
        self.opt = opt
        self.paths_LR = None
        self.paths_HR = None
        self.LR_env = None  # environment for lmdb
        self.HR_env = None

        # read image list from subset list txt
        if opt['image_lists'] is not None and opt['phase'] == 'train':
            print('Read image paths from image_lists')
            self.paths_HR = []
            for ls in opt['image_lists']:
                with open(ls) as f:
                     self.paths_HR.extend([line.rstrip('\n') for line in f])
            self.paths_HR = sorted(self.paths_HR)
        elif opt['subset_file'] is not None and opt['phase'] == 'train':
            with open(opt['subset_file']) as f:
                self.paths_HR = sorted([os.path.join(opt['dataroot_HR'], line.rstrip('\n')) \
                        for line in f])
            if opt['dataroot_LR'] is not None:
                raise NotImplementedError('Now subset only supports generating LR on-the-fly.')
        else:  # read image list from lmdb or image files
            self.HR_env, self.paths_HR = util.get_image_paths(opt['data_type'], opt['dataroot_HR'])
            self.LR_env, self.paths_LR = util.get_image_paths(opt['data_type'], opt['dataroot_LR'])

        assert self.paths_HR, 'Error: HR path is empty.'
        if self.paths_LR and self.paths_HR:
            assert len(self.paths_LR) == len(self.paths_HR), \
                'HR and LR datasets have different number of images - {}, {}.'.format(\
                len(self.paths_LR), len(self.paths_HR))

        if opt['phase'] == 'train':
            self.random_scale_list = opt['random_scale']
        else:
            self.random_scale_list = [1]

        if opt['phase'] == 'train':
            self.LR_random_fuzzy = opt['LR_random_fuzzy']
        else:
            self.LR_random_fuzzy = None

    def __getitem__(self, index):
        HR_path, LR_path = None, None
        scale = self.opt['scale']
        HR_size = self.opt['HR_size']

        # get HR image
        HR_path = self.paths_HR[index]
        img_HR = util.read_img(self.HR_env, HR_path)
        # modcrop in the validation / test phase
        if self.opt['phase'] != 'train':
            img_HR = util.modcrop(img_HR, scale)
        # change color space if necessary
        if self.opt['color']:
            img_HR = util.channel_convert(img_HR.shape[2], self.opt['color'], [img_HR])[0]

        if self.opt['resize'] < 1:
            print('Resize by %.2f' % self.opt['resize'])
            img_HR = cv2.resize(img_HR, None, fx=self.opt['resize'], fy=self.opt['resize'], interpolation=cv2.INTER_LINEAR)

        # print(img_HR.shape)

        # get LR image
        if self.paths_LR:
            LR_path = self.paths_LR[index]
            img_LR = util.read_img(self.LR_env, LR_path)
        else:  # down-sampling on-the-fly
            # randomly scale during training
            if self.opt['phase'] == 'train':
                random_scale = random.choice(self.random_scale_list)
                H_s, W_s, _ = img_HR.shape

                def _mod(n, random_scale, scale, thres):
                    rlt = int(n * random_scale)
                    rlt = (rlt // scale) * scale
                    return thres if rlt < thres else rlt

                H_s = _mod(H_s, random_scale, scale, HR_size)
                W_s = _mod(W_s, random_scale, scale, HR_size)
                img_HR = cv2.resize(np.copy(img_HR), (W_s, H_s), interpolation=cv2.INTER_LINEAR)
                # force to 3 channels
                if img_HR.ndim == 2:
                    img_HR = cv2.cvtColor(img_HR, cv2.COLOR_GRAY2BGR)

            H, W, _ = img_HR.shape

            if self.opt['downsample'] == 'cubic':
                img_LR = cv2.resize(img_HR, dsize=None, fx=1.0/scale, fy=1.0/scale, interpolation=cv2.INTER_CUBIC)
            elif self.opt['downsample'] == 'numpy':
                img_LR = util.imresize_np(img_HR, 1 / scale, True)
            elif self.opt['downsample'] == 'linear':
                img_LR = cv2.resize(img_HR, dsize=None, fx=1.0 / scale, fy=1.0 / scale, interpolation=cv2.INTER_LINEAR)
            if img_LR.ndim == 2:
                img_LR = np.expand_dims(img_LR, axis=2)

        # print(img_HR.shape)
        # print(img_LR.shape)

        if self.opt['phase'] == 'train':
            # if the image size is too small
            H, W, _ = img_HR.shape
            if H < HR_size or W < HR_size or not self.opt['crop']:
                img_HR = cv2.resize(
                    np.copy(img_HR), (HR_size, HR_size), interpolation=cv2.INTER_LINEAR)
                # using matlab imresize
                if self.opt['downsample'] == 'cubic':
                    img_LR = cv2.resize(img_HR, dsize=None, fx=1.0 / scale, fy=1.0 / scale,
                                        interpolation=cv2.INTER_CUBIC)
                elif self.opt['downsample'] == 'numpy':
                    img_LR = util.imresize_np(img_HR, 1 / scale, True)
                elif self.opt['downsample'] == 'linear':
                    img_LR = cv2.resize(img_HR, dsize=None, fx=1.0 / scale, fy=1.0 / scale,
                                        interpolation=cv2.INTER_LINEAR)
                #
                # img_LR = util.imresize_np(img_HR, 1 / scale, True)
                if img_LR.ndim == 2:
                    img_LR = np.expand_dims(img_LR, axis=2)

            H, W, C = img_LR.shape
            LR_size = HR_size // scale

            # randomly crop
            if self.opt['crop']:
                rnd_h = random.randint(0, max(0, H - LR_size))
                rnd_w = random.randint(0, max(0, W - LR_size))
                img_LR = img_LR[rnd_h:rnd_h + LR_size, rnd_w:rnd_w + LR_size, :]
                rnd_h_HR, rnd_w_HR = int(rnd_h * scale), int(rnd_w * scale)
                img_HR = img_HR[rnd_h_HR:rnd_h_HR + HR_size, rnd_w_HR:rnd_w_HR + HR_size, :]

            # augmentation - flip, rotate
            img_LR, img_HR = util.augment([img_LR, img_HR], self.opt['use_flip'], \
                self.opt['use_rot'])

            if self.LR_random_fuzzy is not None:
                random_fuzzy = random.choice(self.LR_random_fuzzy)
                assert self.opt['downsample'] == 'numpy'
                init_LR = np.copy(img_LR)
                img_LR = util.imresize(img_LR, random_fuzzy, True)
                img_LR = util.imresize(img_LR, 1 / random_fuzzy, True)
                if img_LR.shape[0] != LR_size or img_LR.shape[1] != LR_size:
                    print('Warning: LR shape changed after random fuzzy. Using initial one.', img_LR.shape[0], img_LR.shape[1], LR_size)
                    img_LR = init_LR

        # change color space if necessary
        if self.opt['color']:
            img_LR = util.channel_convert(C, self.opt['color'], [img_LR])[0] # TODO during val no definetion

        # BGR to RGB, HWC to CHW, numpy to tensor
        if img_HR.shape[2] == 3:
            img_HR = img_HR[:, :, [2, 1, 0]]
            img_LR = img_LR[:, :, [2, 1, 0]]
        img_HR = torch.from_numpy(np.ascontiguousarray(np.transpose(img_HR, (2, 0, 1)))).float()
        img_LR = torch.from_numpy(np.ascontiguousarray(np.transpose(img_LR, (2, 0, 1)))).float()

        # return: 0-1
        if LR_path is None:
            LR_path = HR_path
        return {'LR': img_LR, 'HR': img_HR, 'LR_path': LR_path, 'HR_path': HR_path}

    def __len__(self):
        return len(self.paths_HR)
