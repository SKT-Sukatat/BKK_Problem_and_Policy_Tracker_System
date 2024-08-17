from airflow.models import DAG, Variable
from airflow.decorators import dag, task
from airflow.operators.python import BranchPythonOperator
from airflow.operators.bash import BashOperator
from airflow.utils.dates import days_ago
from airflow.providers.google.cloud.transfers.gcs_to_bigquery import GCSToBigQueryOperator
from airflow.providers.google.cloud.sensors.gcs import GCSFileSensor


import pandas as pd
from datetime import datetime
import pytz


# Define Input Path
TRAFFY_RECORDS_API = "https://publicapi.traffy.in.th/dump-csv-chadchart/bangkok_traffy.csv"

# Define Output Path
TRAFFY_GCS_BUCKET_PATH	 = Variable.get("TRAFFY_GCS_BUCKET_PATH	")

default_args = {
    'owner':'Sukatat'
}

dag = DAG('Traffy_Data_Pipeline', catchup=False, default_args = default_args)


@task()
def etl_traffy_data(output_path):
    df_traffy_all = pd.read_csv(TRAFFY_RECORDS_API)
    df_traffy_all = df_traffy_all[['ticket_id','timestamp', 'type', 'organization', 'comment', 'photo', 'photo_after',
        'coords', 'address', 'subdistrict', 'district', 'province', 'state', 'star', 'count_reopen', 'last_activity']]
    df_traffy_all.info()

    # Convert the 'timestamp' column to datetime objects
    df_traffy_all['timestamp'] = pd.to_datetime(
        df_traffy_all['timestamp'],
        errors='coerce',        # Invalid parsing will be set as NaT
        infer_datetime_format=True # This helps to infer the format automatically
    )

    # Remove the microseconds and timezone information
    df_traffy_all['timestamp'] = df_traffy_all['timestamp'].dt.floor('S').dt.tz_localize(None)

    # Ensure the column is in datetime64[us] format
    df_traffy_all['timestamp'] = df_traffy_all['timestamp'].astype('datetime64[us]')


    # Convert the 'last_activity' column to datetime objects
    df_traffy_all['last_activity'] = pd.to_datetime(
        df_traffy_all['last_activity'],
        errors='coerce',        # Invalid parsing will be set as NaT
        infer_datetime_format=True # This helps to infer the format automatically
    )

    # Remove the microseconds and timezone information
    df_traffy_all['last_activity'] = df_traffy_all['last_activity'].dt.floor('S').dt.tz_localize(None)

    # Ensure the column is in datetime64[us] format
    df_traffy_all['last_activity'] = df_traffy_all['last_activity'].astype('datetime64[us]')

    # Replace '{' and '}' in the 'type' column
    df_traffy_all['type'] = df_traffy_all['type'].str.replace('{', '', regex=False).str.replace('}', '', regex=False)

    # Drop rows where 'ticket_id' is NaN
    df_traffy_all = df_traffy_all.dropna(subset=['ticket_id', 'type'])

    # Replace ' ' with 'อื่นๆ' in the 'type' column
    df_traffy_all['type'] = df_traffy_all['type'].replace('', 'อื่นๆ')

    # Load Data to GCS
    today = datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d_%m_%Y")
    filename = f'{output_path}Traffy_All_Data_{today}.parquet'
    df_traffy_all.to_parquet(filename, index = False)




@dag(default_args=default_args, schedule_interval="@once", start_date=days_ago(1), tags=['workshop'])
def traffy_pipeline():
    today = datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d-%m-%Y")
    source_object_path = TRAFFY_GCS_BUCKET_PATH  # Path for source object to laod to BigQuery
    
    # Create task
    etl_traffy_record_data = etl_traffy_data(output_path=TRAFFY_GCS_BUCKET_PATH)

    # Sensor to check if the file exists in GCS
    check_gcs_file = GCSFileSensor(
        task_id='check_gcs_file',
        bucket_name='your-bucket-name',  # Replace with your bucket name
        object_name='path/to/your/file.txt',  # Replace with your file path
        timeout=30 * 60,  # Timeout after 30 minutes
        poke_interval=60,  # Check every 60 seconds
        mode='poke',  # Use 'poke' mode for simplicity
    )

    # Function to decide which path to take
    def choose_branch(**kwargs):
        # Based on some condition, decide which branch to take
        if kwargs['ti'].xcom_pull(task_ids='check_gcs_file'):
            return 'file_exists'
        else:
            return 'file_not_exists'

    branch_task = BranchPythonOperator(
        task_id='branch_task',
        python_callable=choose_branch,
    )

    File_Exist_Load_to_BQ = GCSToBigQueryOperator(
        task_id='File_Exist_Load_to_BQ',
        bucket='bkk-policy-data',
        source_objects=[f'{source_object_path}Traffy_All_Data_{today}.parquet'],
        destination_project_dataset_table="Progress_of_Policy.Top_30_Policy",
        skip_leading_rows=1,
        autodetect = True,
        write_disposition='WRITE_APPEND',
        dag=dag
    )

    # Define the EmailOperator for when the file does not exist
    notify_file_not_exists = EmailOperator(
        task_id='notify_file_not_exists',
        to='your-email@example.com',  # Replace with the recipient's email
        subject='File Not Found in GCS',
        html_content="""
        <h3>Alert: File Not Found</h3>
        <p>The file <strong>path/to/your/file.txt</strong> was not found in the GCS bucket <strong>your-bucket-name</strong>.</p>
        <p>Please take the necessary actions.</p>
        """,
    )

    # Crate Task Dependency (Create DAG)
    etl_traffy_record_data >> check_gcs_file >> branch_task >> [File_Exist_Load_to_BQ, notify_file_not_exists]

traffy_pipeline()