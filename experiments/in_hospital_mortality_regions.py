"""
Create experiments for the in-hospital mortality problem using MIMIC and eICU datasets
"""

import pandas as pd
import numpy as np

from MED3pa.datasets import DatasetsManager
from MED3pa.models import BaseModelManager
from MED3pa.med3pa import Med3paExperiment, Med3paDetectronExperiment
from sklearn.impute import KNNImputer

from src.data.processing_in_hospital_mortality import ventilation_correction
from src.data.saps_processing import apply_saps
from src.models.xgboost_classifier import XGBClassifier

# Constants
params = {
    'calibrate': True,  # Whether to apply calibration correction to the BaseModel or not
    'class_weighting': True,  # Whether to apply class weighting correction in the BaseModel or not
    'main_seed': 42,
    'threshold': 'auc',  # Whether to apply threshold correction in the BaseModel or not. Options: ['auc', None]
    'to_saps_score': False  # True to convert variables to Saps scores, False to keep original variable value
}

# Import datasets
mimic_df = pd.read_csv('datasets/in_hospital_mortality/mimic_filtered_data.csv')
eicu_df = pd.read_csv('datasets/in_hospital_mortality/eicu_filtered_data.csv')
group_variable = 'region'
hospitals = pd.read_csv('datasets/in_hospital_mortality/hospital.csv')[['hospitalid', group_variable]]

eicu_df = pd.merge(eicu_df, hospitals, on='hospitalid', how='left')

# Remove hospitalid from mimic
mimic_df = mimic_df.drop(columns=['hospitalid', 'stay_id'])
eicu_df = eicu_df.drop(columns=['stay_id'])

# Apply ventilation correction
mimic_df[['pao2fio2', 'cpap', 'vent']] = mimic_df.apply(lambda row:
                                                        ventilation_correction(row[['pao2fio2', 'cpap', 'vent']]),
                                                        axis=1)
eicu_df[['pao2fio2', 'cpap', 'vent']] = eicu_df.apply(lambda row:
                                                      ventilation_correction(row[['pao2fio2', 'cpap', 'vent']]),
                                                      axis=1)

# ## MIMIC EXPERIMENT
# Initialize data dictionaries
mimic_0813_train = {}
mimic_0813_test = {}
mimic_1416 = {}
mimic_1719 = {}

# split mimic dataset
# # Half of Mimic 2008-2013 to train BaseModel and other half to test with the 3PA method (Internal Validation)
mimic_0813 = mimic_df[(mimic_df['anchor_year_group'] == '2008 - 2010') |
                      (mimic_df['anchor_year_group'] == '2011 - 2013')]
mimic_0813 = mimic_0813.drop(columns=['anchor_year_group'])

mimic_0813_train['x'] = mimic_0813.sample(frac=0.5, random_state=params['main_seed'])
mimic_0813_test['x'] = mimic_0813.drop(mimic_0813_train['x'].index)
mimic_0813_train['x'].reset_index(drop=True, inplace=True)
mimic_0813_test['x'].reset_index(drop=True, inplace=True)
del mimic_0813
mimic_0813_train['y'] = np.array(mimic_0813_train['x'].pop('deceased'))
mimic_0813_test['y'] = np.array(mimic_0813_test['x'].pop('deceased'))

# # Mimic 2014-2016 to test the BaseModel with the 3PA method (Temporal Validation)
mimic_1416['x'] = mimic_df[mimic_df['anchor_year_group'] == '2014 - 2016']
mimic_1416['x'] = mimic_1416['x'].drop(columns=['anchor_year_group'])
mimic_1416['y'] = np.array(mimic_1416['x'].pop('deceased'))

# # Mimic 2017-2019 to test the BaseModel with the 3PA method (Temporal Validation)
mimic_1719['x'] = mimic_df[mimic_df['anchor_year_group'] == '2017 - 2019']
mimic_1719['x'] = mimic_1719['x'].drop(columns=['anchor_year_group'])
mimic_1719['y'] = np.array(mimic_1719['x'].pop('deceased'))

# K-NN imputation
imputer = KNNImputer(n_neighbors=20)

mimic_0813_train['x'] = pd.DataFrame(imputer.fit_transform(mimic_0813_train['x']),
                                     columns=mimic_0813_train['x'].columns)
mimic_0813_test['x'] = pd.DataFrame(imputer.transform(mimic_0813_test['x']), columns=mimic_0813_test['x'].columns)
mimic_1416['x'] = pd.DataFrame(imputer.fit_transform(mimic_1416['x']), columns=mimic_1416['x'].columns)
mimic_1719['x'] = pd.DataFrame(imputer.fit_transform(mimic_1719['x']), columns=mimic_1719['x'].columns)

# Round imputed Binary variables
variables_to_round = ['mets', 'hem', 'aids', 'cpap', 'vent']
for df in [mimic_0813_train, mimic_0813_test, mimic_1416, mimic_1719]:
    df['x'][variables_to_round] = df['x'][variables_to_round].round()

# Apply saps transformation on datasets
for df in [mimic_0813_train, mimic_0813_test, mimic_1416, mimic_1719]:
    df['x'] = apply_saps(df['x'], params['to_saps_score'])

# Train BaseModel
baseMdl = XGBClassifier(objective='binary:logistic',
                        random_state=params['main_seed'],
                        class_weighting=params['class_weighting'])
baseMdl.fit(mimic_0813_train['x'], mimic_0813_train['y'],
            threshold=params['threshold'],
            calibrate=params['calibrate'],
            n_trials=200)

# Get BaseModel global performance metrics
baseMdlRes = baseMdl.evaluate(mimic_0813_test['x'], mimic_0813_test['y'])

# ## MED3pa section
# Set the base model using BaseModelManager
base_model_manager = BaseModelManager()
base_model_manager.set_base_model(model=baseMdl)

# Define parameters for the experiment
ipc_params = {'n_estimators': 100}
ipc_grid = {
    'n_estimators': [50, 100, 200],
    'max_depth': [2, 3, 4, 5],
    'min_samples_leaf': [1, 2, 4]

}
apc_grid = {
    # 'max_depth': [2, 3, 4, 5],
    'min_samples_leaf': [1, 2, 4]
}
apc_params = {'max_depth': 6}

# # Loop over datasets
# for data_name, data_dict in {'Internal': mimic_0813_test,
#                              'Temporal_1416': mimic_1416,
#                              'Temporal_1719': mimic_1719}.items():
#     # Initialize the DatasetsManager
#     datasets = DatasetsManager()
#
#     # Load datasets for testing
#     if data_name == "Internal":
#         # # Reference set where the BaseModel was tested
#         # datasets.set_from_data(dataset_type="reference", observations=mimic_0813_train['x'].to_numpy(),
#         #                        true_labels=mimic_0813_train['y'],
#         #                        column_labels=mimic_0813_train['x'].columns)
#         # Testing set where the BaseModel is tested
#         datasets.set_from_data(dataset_type="testing", observations=data_dict['x'].to_numpy(),
#                                true_labels=data_dict['y'],
#                                column_labels=data_dict['x'].columns)
#     else:
#         # training set where the BaseModel was trained
#         datasets.set_from_data(dataset_type="training", observations=mimic_0813_train['x'].to_numpy(),
#                                true_labels=mimic_0813_train['y'],
#                                column_labels=mimic_0813_train['x'].columns)
#         # validation set where the BaseModel was validated
#         # datasets.set_from_data(dataset_type="validation", observations=mimic_0813_train['x'].to_numpy(),
#         #                        true_labels=mimic_0813_train['y'],
#         #                        column_labels=mimic_0813_train['x'].columns)
#         # reference set where the BaseModel was tested
#         datasets.set_from_data(dataset_type="reference", observations=mimic_0813_test['x'].to_numpy(),
#                                true_labels=mimic_0813_test['y'],
#                                column_labels=mimic_0813_test['x'].columns)
#         # testing set where we test the BaseModel
#         datasets.set_from_data(dataset_type="testing", observations=data_dict['x'].to_numpy(),
#                                true_labels=data_dict['y'],
#                                column_labels=data_dict['x'].columns)
#
#
#     # Execute the MED3PA experiment
#     results = Med3paExperiment.run(
#         datasets_manager=datasets,
#         base_model_manager=base_model_manager,
#         uncertainty_metric="sigmoidal_error",
#         ipc_type='EnsembleRandomForestRegressor',
#         ipc_params=ipc_params,
#         apc_params=apc_params,
#         ipc_grid_params=ipc_grid,
#         apc_grid_params=apc_grid,
#         samples_ratio_min=0,
#         samples_ratio_max=10,
#         samples_ratio_step=5,
#         evaluate_models=True,
#     )
#
#     # Save the results to a specified directory
#     results.save(file_path=f'experiments/results/in_hospital_mortality/{data_name}')
#
#     # Execute the Med3pa experiment with Detectron results
#     if data_name != "Internal":
#         med3pa_detectron_results = Med3paDetectronExperiment.run(
#             datasets=datasets,
#             base_model_manager=base_model_manager,
#             uncertainty_metric="sigmoidal_error",
#             ipc_type='EnsembleRandomForestRegressor',
#             ipc_params=ipc_params,
#             apc_params=apc_params,
#             ipc_grid_params=ipc_grid,
#             apc_grid_params=apc_grid,
#             samples_size=20,
#             ensemble_size=10,
#             num_calibration_runs=100,
#             patience=3,
#             test_strategies=["original_disagreement_strategy", "mannwhitney_strategy",
#                              "enhanced_disagreement_strategy"],
#             allow_margin=False,
#             margin=0.05,
#             samples_ratio_min=0,
#             samples_ratio_max=10,
#             samples_ratio_step=5,
#             evaluate_models=True,
#         )
#
#         # Save the results to a specified directory
#         med3pa_detectron_results.save(file_path=f'experiments/results/in_hospital_mortality/with_detectron/{data_name}')

# ## eICU EXPERIMENT USING MIMIC-TRAINED BASEMODEL
# Split eICU dataset by hospitalid
eicu_df = eicu_df.drop(columns=['hospitalid'])
eicu_groupings = eicu_df[group_variable].unique()

# Apply the same preprocessing steps as MIMIC for each hospital
for group_id in eicu_groupings:
    eicu_hospital_df = eicu_df[eicu_df[group_variable] == group_id].drop(columns=[group_variable])
    samples_size = min(20, len(eicu_hospital_df))
    if len(eicu_hospital_df) >= 20:

        # Split into testing set (no need for training since we're using the MIMIC-trained model)
        eicu_test = {}
        eicu_test['x'] = eicu_hospital_df.reset_index(drop=True)
        eicu_test['y'] = np.array(eicu_test['x'].pop('deceased'))

        # K-NN Imputation on eICU testing data
        eicu_test['x'] = pd.DataFrame(imputer.transform(eicu_test['x']),
                                      columns=eicu_test['x'].columns)

        # Round imputed binary variables
        eicu_test['x'][variables_to_round] = eicu_test['x'][variables_to_round].round()

        # Apply SAPS transformation
        eicu_test['x'] = apply_saps(eicu_test['x'], params['to_saps_score'])

        # Initialize DatasetsManager for eICU
        datasets = DatasetsManager()

        # Load the MIMIC training and reference data (same for all hospitals)
        datasets.set_from_data(dataset_type="training", observations=mimic_0813_train['x'].to_numpy(),
                               true_labels=mimic_0813_train['y'],
                               column_labels=mimic_0813_train['x'].columns)
        datasets.set_from_data(dataset_type="reference", observations=mimic_0813_test['x'].to_numpy(),
                               true_labels=mimic_0813_test['y'],
                               column_labels=mimic_0813_test['x'].columns)

        # Set eICU hospital-specific test data as 'testing'
        datasets.set_from_data(dataset_type="testing", observations=eicu_test['x'].to_numpy(),
                               true_labels=eicu_test['y'],
                               column_labels=eicu_test['x'].columns)

        # Execute the MED3PA experiment for the current eICU hospital
        results = Med3paExperiment.run(
            datasets_manager=datasets,
            base_model_manager=base_model_manager,  # Use the already-trained MIMIC BaseModel
            uncertainty_metric="sigmoidal_error",
            ipc_type='EnsembleRandomForestRegressor',
            ipc_params=ipc_params,
            apc_params=apc_params,
            ipc_grid_params=ipc_grid,
            apc_grid_params=apc_grid,
            samples_ratio_min=0,
            samples_ratio_max=10,
            samples_ratio_step=5,
            evaluate_models=True,
        )

        # Save results for the current hospital
        results.save(file_path=f'experiments/results/in_hospital_mortality/External/eicu_hospital_{group_id}')

        # Optionally run MED3PA Detectron experiment
        med3pa_detectron_results = Med3paDetectronExperiment.run(
            datasets=datasets,
            base_model_manager=base_model_manager,  # Use the already-trained MIMIC BaseModel
            uncertainty_metric="sigmoidal_error",
            ipc_type='EnsembleRandomForestRegressor',
            ipc_params=ipc_params,
            apc_params=apc_params,
            ipc_grid_params=ipc_grid,
            apc_grid_params=apc_grid,
            samples_size=samples_size,
            ensemble_size=10,
            num_calibration_runs=100,
            patience=3,
            test_strategies=["original_disagreement_strategy", "mannwhitney_strategy",
                             "enhanced_disagreement_strategy"],
            allow_margin=False,
            margin=0.05,
            samples_ratio_min=0,
            samples_ratio_max=10,
            samples_ratio_step=5,
            evaluate_models=True,
        )

        # Save the Detectron results for the current hospital
        med3pa_detectron_results.save(file_path=f'experiments/results/in_hospital_mortality/External/with_detectron'
                                                f'/eicu_hospital_{group_id}')

