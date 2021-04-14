"""
Module description:

"""

__version__ = '0.1'
__author__ = 'Vito Walter Anelli, Claudio Pomo'
__email__ = 'vitowalter.anelli@poliba.it, claudio.pomo@poliba.it'

import numpy as np
import pickle
import time
import scipy.sparse as sparse
from sklearn.metrics.pairwise import cosine_similarity
from elliot.recommender.recommender_utils_mixin import RecMixin
from elliot.utils.write import store_recommendation
from sklearn.preprocessing import normalize
from elliot.recommender.base_recommender_model import BaseRecommenderModel
from elliot.recommender.NN.item_knn.item_knn_similarity import Similarity
from elliot.recommender.NN.item_knn.aiolli_ferrari import AiolliSimilarity
from elliot.recommender.base_recommender_model import init_charger

np.random.seed(42)


class EASER(RecMixin, BaseRecommenderModel):

    @init_charger
    def __init__(self, data, config, params, *args, **kwargs):
        self._random = np.random

        self._params_list = [
            ("_neighborhood", "neighborhood", "neighborhood", -1, int, None),
            ("_l2_norm", "l2_norm", "l2_norm", 1e3, float, None)
        ]

        self.autoset_params()
        if self._neighborhood == -1:
            self._neighborhood = self._data.num_items

    @property
    def name(self):
        return f"EASER_{self.get_params_shortcut()}"

    def get_recommendations(self, k: int = 10):
        data, rows_indices, cols_indptr = [], [], []

        k = min(k, len(self._data.items))

        column_row_index = np.arange(len(self._data.items), dtype=np.int32)

        for item_idx in range(len(self._data.items)):
            cols_indptr.append(len(data))
            column_data = self._similarity_matrix[:, item_idx]

            non_zero_data = column_data != 0

            idx_sorted = np.argsort(column_data[non_zero_data])  # sort by column
            top_k_idx = idx_sorted[-k:]

            data.extend(column_data[non_zero_data][top_k_idx])
            rows_indices.extend(column_row_index[non_zero_data][top_k_idx])

        cols_indptr.append(len(data))

        W_sparse = sparse.csc_matrix((data, rows_indices, cols_indptr),
                                     shape=(len(self._data.items), len(self._data.items)), dtype=np.float32).tocsr()


        # items_ratings_pair = [list(zip(map(self._data.private_items.get, u_list[0]), u_list[1]))
        #                       for u_list in list(zip(i.numpy(), v.numpy()))]

        return {u: self.get_user_predictions(u, W_sparse) for u in self._data.train_dict.keys()}

    def get_user_predictions(self, user_id, W_sparse):
        user_id = self._data.public_users.get(user_id)
        b = self._train[user_id].dot(W_sparse)
        a = self.get_train_mask(user_id, user_id+1)
        b[a] = -np.inf
        indices, values = zip(*[(self._data.private_items.get(u_list[0]), u_list[1])
                              for u_list in enumerate(b.data)])

        indices = np.array(indices)
        values = np.array(values)
        local_k = min(self.evaluator.get_needed_recommendations(), len(values))
        partially_ordered_preds_indices = np.argpartition(values, -local_k)[-local_k:]
        real_values = values[partially_ordered_preds_indices]
        real_indices = indices[partially_ordered_preds_indices]
        local_top_k = real_values.argsort()[::-1]
        return [(real_indices[item], real_values[item]) for item in local_top_k]

    def train(self):
        if self._restore:
            return self.restore_weights()

        start = time.time()

        self._train = normalize(self._data.sp_i_train_ratings, norm='l2', axis=1)
        self._train = normalize(self._train, norm='l2', axis=0)

        self._similarity_matrix = np.empty((len(self._data.items), len(self._data.items)))
        self._similarity_matrix = cosine_similarity(self._train.T)

        diagonal_indices = np.diag_indices(self._similarity_matrix.shape[0])
        item_popularity = np.ediff1d(self._train.tocsc().indptr)
        self._similarity_matrix[diagonal_indices] = item_popularity + self._l2_norm

        P = np.linalg.inv(self._similarity_matrix)

        self._similarity_matrix = P / (-np.diag(P))

        self._similarity_matrix[diagonal_indices] = 0.0

        end = time.time()
        print(f"The similarity computation has taken: {end - start}")

        best_metric_value = 0

        recs = self.get_recommendations(self._neighborhood)
        result_dict = self.evaluator.eval(recs)
        self._results.append(result_dict)
        print(f'Finished')

        if self._results[-1][self._validation_k]["val_results"][self._validation_metric] > best_metric_value:
            print("******************************************")
            if self._save_weights:
                with open(self._saving_filepath, "wb") as f:
                    pickle.dump(self._model.get_model_state(), f)
            if self._save_recs:
                store_recommendation(recs, self._config.path_output_rec_result + f"{self.name}.tsv")