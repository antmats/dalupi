import numpy as np
from functools import partial
import os
import pandas 
import torch
import pickle
from PIL import Image
from torch.utils.data import Dataset

CELEBA_ROOT = '/mimer/NOBACKUP/groups/lupida/data/celeb'

attribute_names = ['5_o_Clock_Shadow', 'Arched_Eyebrows', 'Attractive', 'Bags_Under_Eyes',
                   'Bald', 'Bangs', 'Big_Lips', 'Big_Nose', 'Black_Hair', 'Blond_Hair',
                   'Blurry', 'Brown_Hair', 'Bushy_Eyebrows', 'Chubby', 'Double_Chin',
                   'Eyeglasses', 'Goatee', 'Gray_Hair', 'Heavy_Makeup', 'High_Cheekbones',
                   'Male', 'Mouth_Slightly_Open', 'Mustache', 'Narrow_Eyes', 'No_Beard',
                   'Oval_Face', 'Pale_Skin', 'Pointy_Nose', 'Receding_Hairline',
                   'Rosy_Cheeks', 'Sideburns', 'Smiling', 'Straight_Hair', 'Wavy_Hair',
                   'Wearing_Earrings', 'Wearing_Hat', 'Wearing_Lipstick',
                   'Wearing_Necklace', 'Wearing_Necktie', 'Young']

# https://github.com/p-lambda/in-n-out/blob/main/innout/datasets/celeba.py#L38
def invert_list(l):
    """Return a list that inverts the mapping of l."""
    dict = {}
    for i, x in zip(range(len(l)), l):
        dict[x] = i
    return dict

attr_name_to_idx = invert_list(attribute_names)

domain_selectors = {
    'wearing_hat': lambda a: a[attr_name_to_idx['Wearing_Hat']],
    'not_wearing_hat': lambda a: 1 - a[attr_name_to_idx['Wearing_Hat']],
}

# https://github.com/p-lambda/in-n-out/blob/main/innout/datasets/celeba.py#L55
def select_indices(attributes, attribute_selector):
    """Return a list of indices i where attribute_selector(attributes[i]) is True."""
    mask = np.apply_along_axis(attribute_selector, axis=1, arr=attributes)
    return np.argwhere(mask)

# https://github.com/p-lambda/in-n-out/blob/main/innout/datasets/celeba.py#L61
def sample_indices(indices, target, pos_fraction, num_points, rng):
    # First split into two groups, positive and negative
    num_pos = round(num_points * pos_fraction)
    num_neg = num_points - num_pos
    pos_indices = indices[target == 1]
    neg_indices = indices[target == 0]
    rng.shuffle(pos_indices)
    rng.shuffle(neg_indices)
    indices = np.concatenate([pos_indices[:num_pos], neg_indices[:num_neg]])
    rng.shuffle(indices)
    return indices

# https://github.com/p-lambda/in-n-out/blob/main/innout/datasets/__init__.py#L16
class RangeDataset(Dataset):
    '''
    Takes a range over another dataset
    '''
    def __init__(self, dataset, start_idx, end_idx):
        self.dataset = dataset
        self.start_idx = start_idx
        self.end_idx = end_idx
        if start_idx >= len(self.dataset):
            raise ValueError(f"start index must be less than length of dataset {len(self.dataset)}")
        if end_idx > len(self.dataset):
            raise ValueError(f"end index must be less than or equal to length of dataset {len(self.dataset)}")

    def __getitem__(self, idx):
        idx += self.start_idx
        return self.dataset[idx]

    def __len__(self):
        return self.end_idx - self.start_idx

# https://github.com/p-lambda/in-n-out/blob/main/innout/datasets/celeba.py#L95
class CelebA(Dataset):
    def __init__(self, seed, target_attribute, meta_attributes, split, 
                 num_in_labeled, num_in_unlabeled, num_in_val, num_in_test,
                 num_out_labeled, num_out_unlabeled, num_out_test,
                 pos_fraction, in_domain_selector, out_domain_selector,
                 use_unlabeled_id=False, use_unlabeled_ood=False,
                 in_labeled_splits=None, in_labeled_split_idx=None, transform=None,
                 meta_as_input=False, only_meta=False, meta_as_target=False,
                 celeba_root=CELEBA_ROOT, pickle_file_path=None, unlabeled_target_path=None):
        """
        Args:
            seed: integer seed for shuffling dataset.
            target_attribute: an integer between 0 and 39 (inclusive), or a string in
                attribute_names representing the attribute that we want to predict.
            meta_attributes: list of attributes each specified by an integer between 0 and 39
                (inclusive), or a string in attribute_names. These attribute values will be
                be supplied to the algorithm as metadata, even with unlabeled data.
            split:
                'in_labeled': labeled data from the in-domain.
                'in_unlabeled': unlabeled data from in-domain.
                'in_val': labeled validation data from in-domain.
                'in_test': labeled test data from in-domain.
                'out_labeled': labeled adaptation data from the out-domain.
                'out_unlabeled': unlabeled adaptation data from the out-domain.
                'out_test': labeled test data from the out-domain.
                'all_unlabeled': use unlabeled from both in and out domain.
                'train': alias for in_labeled.
                'val': alias for in_val.
                'test': alias for in_test.
                'test2': alias for out_test.
            use_unlabeled_id: Use unlabeled data from ID.
            use_unlabeled_ood: Use unlabeled data from OOD, appended after ID if ID specified.
            num_in_labeled: positive integer number of labeled in-domain training examples.
            num_in_unlabeled: positive integer number of *unlabeled* in-domain examples.
            num_in_val: positive integer number of labeled in-domain validation examples.
            num_in_test: positive integer number of labeled in-domain test examples.
            num_out_labeled: positive integer number of labeled out-domain adaptation examples.
            num_out_unlabeled: positive integer number of *unlabeled* out-domain adaptation examples.
            num_out_test: positive integer number of labeled out-domain test examples.
            pos_fraction: float between 0.0 and 1.0 representing the proportion of examples
                that are positive (have target_attribute == 1).
            in_domain_selector: a string (in domain_selectors) representing the attributes
                that in-domain examples have. For example, we might want all in-domain
                examples to have hats. The train and val will be taken in-domain.
            out_domain_selector: a string (in domain_selectors) representing the attributes
                that out of domain examples have. For example, we might want all out of domain
                examples to not have hats. The test set will be selected to be out of domain.
            in_labeled_splits: a list of integers that sum to num_in_labeled, indiciating how we
                should further sub-divide the in-domain labeled examples e.g. for 2 stage training
                where we train part of the neural net on some data, and fine tune another part
                on held out data. None if we don't split the in_labeled set.
            in_labeled_split_idx: a integer index into in_labeled_splits, representing which split
                we use, or None if we should use the entire in_labeled data. Both
                in_labeled_split_idx and must not be None to use in_labeled splits.
            transform: a transform that will be applied to all images.
            only_meta: Only output the metadata / attributes as inputs (not the image). If
                only_meta is True, then meta_as_input should be true.
            meta_as_target: Use the metadata as targets instead of the target attribute. This
                could be useful e.g. if we are pretraining on the metadata.
            celeba_root: path to folder containing celeba files, e.g. this folder contains
                identity_CelebA.txt, img_align_celeba, etc.
            pickle_file_path: if not None, then read a pickle file containing all the celeba
                images. This can be useful so we don't repeatedly hit the distributed file system
                for every call to read an image.
            unlabeled_target_path: Path to load unlabeled targets from.
        """
        self._rng = np.random.RandomState(seed)
        if not in_domain_selector in domain_selectors:
            raise ValueError('In domain selector {} is not valid.'.format(in_domain_selector))
        if not out_domain_selector in domain_selectors:
            raise ValueError('Out domain selector {} is not valid.'.format(out_domain_selector))
        self._celeba_root = celeba_root
        fn = partial(os.path.join, self._celeba_root)
        self._split = split
        celeba_splits = pandas.read_csv(
            fn("list_eval_partition.txt"), delim_whitespace=True, header=None, index_col=0)
        mask = (celeba_splits[1] == 0)  # Use celeba training set.
        self._pickle_file_path = pickle_file_path
        if pickle_file_path is None:
            self._filenames = celeba_splits[mask].index.values
        else:
            self._images = pickle.load(open(pickle_file_path, "rb"))
        attr = pandas.read_csv(fn("list_attr_celeba.txt"), delim_whitespace=True, header=1)
        self._attr = torch.as_tensor(attr[mask].values)
        self._attr = torch.div(self._attr + 1, 2, rounding_mode='floor')  # map from {-1, 1} to {0, 1}
        self._attr_names = list(attr.columns)
        assert self._attr_names == attribute_names
        self._transform = transform
        self._meta_as_input = meta_as_input
        self._only_meta = only_meta
        if self._only_meta and (not self._meta_as_input):
            raise ValueError('If only_meta=True, then meta_as_input must be True.')
        self._meta_as_target = meta_as_target

        # Convert meta-attributes and target attribute to indices if they are strings.
        def convert_attr_desc_to_index(attr_desc):
            if type(attr_desc) == str:
                if not attr_desc in attribute_names:
                    raise ValueError('Attributes specified must be a valid string or integer between 0 and'
                                     ' 39 inclusive but was {}'.format(attr_desc))
                return attr_name_to_idx[attr_desc]
            else:
                if not(0 <= attr_desc <= 39):
                        raise ValueError('Attributes specified must be a valid string or integer between 0 and'
                                         ' 39 inclusive but was {}'.format(attr_desc))
                return attr_desc
        self._meta_attributes = [convert_attr_desc_to_index(a) for a in meta_attributes]
        self._target_attribute = convert_attr_desc_to_index(target_attribute)
        
        # Get the list of indices for in-domain and out-domain, check that they don't overlap.
        in_indices = select_indices(self._attr, domain_selectors[in_domain_selector])
        out_indices = select_indices(self._attr, domain_selectors[out_domain_selector])
        if len(np.intersect1d(in_indices, out_indices)) > 0:
            raise ValueError('in_domain_selector and out_domain_selector must not overlap.')
        # Extract the val and test sets for in-domain using a fixed seed.
        test_seed = 0
        test_rng = np.random.RandomState(seed=test_seed)
        in_target_values = self._attr[in_indices, self._target_attribute]
        in_eval_indices = sample_indices(in_indices, in_target_values,
                                         pos_fraction, num_in_val+num_in_test, test_rng)
        in_indices_no_test = np.setdiff1d(in_indices, in_eval_indices)
        in_val_indices = in_eval_indices[:num_in_val]
        in_test_indices = in_eval_indices[num_in_val:]
        assert(len(in_test_indices) == num_in_test)
        # Extract the test set for out-domain using a fixed seed.
        test_rng = np.random.RandomState(seed=test_seed)
        out_target_values = self._attr[out_indices, self._target_attribute]
        out_test_indices = sample_indices(out_indices, out_target_values,
                                          pos_fraction, num_out_test, test_rng)
        out_indices_no_test = np.setdiff1d(out_indices, out_test_indices)
        # Use the user specified seed to extract the other 4 sets.
        def get_labeled_unlabeled(indices, num_labeled, num_unlabeled):
            target_values = self._attr[indices, self._target_attribute]
            sampled_indices = sample_indices(indices, target_values, pos_fraction,
                                             num_labeled+num_unlabeled, self._rng)
            labeled_indices = sampled_indices[:num_labeled]
            unlabeled_indices = sampled_indices[num_labeled:]
            assert(len(unlabeled_indices) == num_unlabeled)
            return labeled_indices, unlabeled_indices
        in_labeled_indices, in_unlabeled_indices = get_labeled_unlabeled(
            in_indices_no_test, num_in_labeled, num_in_unlabeled)
        out_labeled_indices, out_unlabeled_indices = get_labeled_unlabeled(
            out_indices_no_test, num_out_labeled, num_out_unlabeled)

        # Choose the current set of indices based on the split mode.
        self._only_unlabeled = False
        self.pseudolabels = None
        def load_and_check_unlabeled_target_path():
            self.pseudolabels = np.load(unlabeled_target_path)
            if not(len(self.pseudolabels) + self._labeled_len == len(self._indices)):
                raise ValueError(
                    f'Require pseudolabel len ({len(self.pseudolabels)}) + '
                    f'labeled len ({self._labeled_len}) == '
                    f'total no. of indices ({len(self._indices)})')
        if self._split == 'in_labeled' or self._split == 'train':
            self._indices = in_labeled_indices
            # Construct additional splits if needed.
            if in_labeled_splits is not None and in_labeled_split_idx is not None:
                if sum(in_labeled_splits) != num_in_labeled:
                    raise ValueError('in_labeled_splits must sum up to num_in_labeled.')
                if not (0 <= in_labeled_split_idx < len(in_labeled_splits)):
                    raise ValueError('in_labeled_split_idx must be between 0 '
                                     'and len(in_labeled_splits) - 1 inclusive.')
                cum_splits = np.cumsum([0] + in_labeled_splits)
                start_idx = cum_splits[in_labeled_split_idx]
                end_idx = cum_splits[in_labeled_split_idx+1]
                self._indices = in_labeled_indices[start_idx:end_idx]
        elif self._split == 'in_unlabeled':
            self._indices = in_unlabeled_indices
            self._only_unlabeled = True
        elif self._split == 'in_val' or self._split == 'val':
            self._indices = in_val_indices
        elif self._split == 'in_test' or self._split == 'test':
            self._indices = in_test_indices
        elif self._split == 'out_labeled':
            self._indices = out_labeled_indices
        elif self._split == 'out_unlabeled':
            self._indices = out_unlabeled_indices
            self._only_unlabeled = True
        elif self._split == 'all_unlabeled':
            self._indices = np.concatenate([in_unlabeled_indices, out_unlabeled_indices])
            self._only_unlabeled = True
        elif self._split == 'out_test' or self._split == 'test2':
            self._indices = out_test_indices
        else:
            raise ValueError('split {} not supported'.format(self._split))

        if use_unlabeled_id and use_unlabeled_ood:
            self._labeled_len = len(in_labeled_indices)
            self._indices = np.concatenate(
                [self._indices, in_unlabeled_indices, out_unlabeled_indices])
            if unlabeled_target_path is not None:
                load_and_check_unlabeled_target_path()
        elif use_unlabeled_id:
            self._labeled_len = len(in_labeled_indices)
            self._indices = np.concatenate([self._indices, in_unlabeled_indices])
            if unlabeled_target_path is not None:
                load_and_check_unlabeled_target_path()
        elif use_unlabeled_ood:
            self._labeled_len = len(in_labeled_indices)
            self._indices = np.concatenate([self._indices, out_unlabeled_indices])
            if unlabeled_target_path is not None:
                load_and_check_unlabeled_target_path()

    def __getitem__(self, i):
        index = self._indices[i]
        if self._pickle_file_path is None:
            x = Image.open(os.path.join(self._celeba_root, "img_align_celeba", self._filenames[index]))
        else:
            x = self._images[index]
        if self._transform is not None:
            x = self._transform(x)
        output_dict = {}
        domain_label = self._attr[index][self._meta_attributes]
        if self._meta_as_input:
            if self._only_meta:
                output_dict['data'] = domain_label.float()
            else:
                output_dict['data'] = (x, domain_label.float())
        else:
            output_dict['data'] = x
        is_labeled_example = True
        if not self._only_unlabeled:
            if self.pseudolabels is not None:
                # semi-sup dataset
                if i < self.labeled_len:
                    # in the labeled part
                    output_dict['target'] = self._attr[index][self._target_attribute].unsqueeze(-1).float()
                else:
                    is_labeled_example = False
                    # in the pseudo labeled part
                    output_dict['target'] = torch.Tensor([self.pseudolabels[i - self.labeled_len].squeeze()]).float()
            else:
                output_dict['target'] = self._attr[index][self._target_attribute].unsqueeze(-1).float()
        else:
            is_labeled_example = False
            output_dict['target'] = -1.0
        output_dict['domain_label'] = {'meta': self._attr[index][self._meta_attributes], 'labeled': is_labeled_example}
        if self._meta_as_target:
            output_dict['target'] = output_dict['domain_label']['meta'].float()
        return output_dict

    def get_unlabeled_dataset(self):
        if self._only_unlabeled:
            return self
        else:
            return RangeDataset(self, self._labeled_len, len(self))

    def __len__(self) -> int:
        return len(self._indices)
    
class BaseDataset(torch.utils.data.Dataset):
    def __init__(self, X, y):
        super().__init__()
        self.X = X
        self.y = y
        
    @property
    def class_labels(self):
        return ['0', '1']
    
    def describe(self, *args, **kwargs):
        pass
    
    def decide_transform(self, *args, **kwargs):
        pass

    def __len__(self):
        return len(self.X)
    
    def __getitem__(self, i):
        Xi = self.X[i]
        yi = self.y[i] if self.y is not None else torch.Tensor([float('nan')])
        return Xi, yi
