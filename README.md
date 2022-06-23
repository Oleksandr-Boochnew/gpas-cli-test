 ![Tests](https://github.com/GlobalPathogenAnalysisService/gpas-cli/actions/workflows/test.yml/badge.svg) [![PyPI version](https://badge.fury.io/py/gpas.svg)](https://badge.fury.io/py/gpas)

A standalone command line and Python API client for interacting with the Global Pathogen Analysis Service. Tested on Linux, MacOS, with Windows support planned. Uses Python 3.10+

**Progress**

| Command line interface | Python API |
| ----------------- | ------- |
| ✅ `gpas upload` | ✅ `lib.Batch(upload_csv, token).upload()` |
| ✅ `gpas download` | ✅ `lib.download_async()` |
| ✅ `gpas validate` | ✅ `validation.validate()` |
| ✅ `gpas status` | ✅ `lib.fetch_status()`, `lib.fetch_status_async()` |



## Install

###  With `conda`


```shell
curl https://raw.githubusercontent.com/GlobalPathogenAnalysisService/gpas-cli/main/environment.yml --output environment.yml
conda env create -f environment.yml
conda activate gpas-cli
pip install gpas==0.1.0  # If you'd like a versioned release
```

### With `pip`

Install Samtools and [read-it-and-keep](https://github.com/GlobalPathogenAnalysisService/read-it-and-keep) manually

```shell
pip install gpas
# Tell gpas-cli where you installed samtools and read-it-and-keep
export GPAS_SAMTOOLS_PATH=path/to/samtools
export GPAS_READITANDKEEP_PATH=path/to/readItAndKeep
```

## Authentication

Most gpas-cli actions require a valid API token (`token.json`). This can be saved using the 'Get API token' button on the `Upload Client` page of the GPAS portal. If you can't see this button, please ask the team to enable it for you.

## Command line usage

### `gpas validate`

Validates an `upload_csv` and checks that the fastq or bam files it references exist.

```shell
gpas validate large-nanopore-fastq.csv

# Validate supplied tags
gpas validate --environment dev --token token.json large-nanopore-fastq.csv
```

```
% gpas validate -h
usage: gpas validate [-h] [--token TOKEN] [--environment {dev,staging,prod}] [--json-messages] upload_csv

Validate an upload CSV. Validates tags remotely if supplied with an authentication token

positional arguments:
  upload_csv            Path of upload CSV

options:
  -h, --help            show this help message and exit
  --token TOKEN         Path of auth token available from GPAS Portal
                        (default: None)
  --environment {dev,staging,prod}
                        GPAS environment to use
                        (default: prod)
  --json-messages       Emit JSON to stdout
                        (default: False)
```

### `gpas upload`

Validates, decontaminates and upload reads specified in `upload_csv` to the specified GPAS environment

```shell
gpas upload --environment dev --token token.json large-illumina-bam.csv

# Dry run; skip submission
gpas upload --dry-run --environment dev --token token.json large-illumina-bam.csv

# Offline mode; quit after decontamination
gpas upload tests/test-data/large-nanopore-fastq.csv
```

```
% gpas upload -h
usage: gpas upload [-h] [--token TOKEN] [--working-dir WORKING_DIR] [--out-dir OUT_DIR] [--processes PROCESSES] [--dry-run]
                   [--debug] [--environment {dev,staging,prod}] [--json-messages]
                   upload_csv

Validate, decontaminate and upload reads to the GPAS platform

positional arguments:
  upload_csv            Path of upload csv

options:
  -h, --help            show this help message and exit
  --token TOKEN         Path of auth token available from GPAS Portal
                        (default: None)
  --working-dir WORKING_DIR
                        Path of directory in which to make intermediate files
                        (default: /tmp)
  --out-dir OUT_DIR     Path of directory in which to save mapping CSV
                        (default: .)
  --processes PROCESSES
                        Number of tasks to execute in parallel. 0 = auto
                        (default: 0)
  --dry-run             Exit before submitting files
                        (default: False)
  --debug               Emit verbose debug messages
                        (default: False)
  --environment {dev,staging,prod}
                        GPAS environment to use
                        (default: prod)
  --json-messages       Emit JSON to stdout
                        (default: False)
```

### `gpas download`

Downloads `json`, `fasta`, `vcf` and `bam` outputs from the GPAS platform by passing either a `mapping_csv` generated during batch upload, or a comma-separated list of sample guids. By passing both `--mapping-csv` and `--rename`, output files are saved using local sample names without the platform's knowledge.

```shell
# Download and rename BAMs for a previous upload
gpas download --rename --mapping-csv example_mapping.csv --file-types bam token.json

# Download all outputs for a single guid
gpas download --guids 6e024eb1-432c-4b1b-8f57-3911fe87555f --file-types json,vcf,bam,fasta token.json
```

```
% gpas download -h
usage: gpas download [-h] [--mapping-csv MAPPING_CSV] [--guids GUIDS] [--file-types FILE_TYPES] [--out-dir OUT_DIR] [--rename]
                     [--debug] [--environment {dev,staging,prod}]
                     token

Download analytical outputs from the GPAS platform for given a mapping csv or list of guids

positional arguments:
  token                 Path of auth token (available from GPAS Portal)

options:
  -h, --help            show this help message and exit
  --mapping-csv MAPPING_CSV
                        Path of mapping CSV generated at upload time
                        (default: None)
  --guids GUIDS         Comma-separated list of GPAS sample guids
                        (default: )
  --file-types FILE_TYPES
                        Comma separated list of outputs to download (json,fasta,bam,vcf)
                        (default: fasta)
  --out-dir OUT_DIR     Path of output directory
                        (default: /Users/bede/Research/Git/gpas-cli)
  --rename              Rename outputs using local sample names (requires --mapping-csv)
                        (default: False)
  --debug               Emit verbose debug messages
                        (default: False)
  --environment {dev,staging,prod}
                        GPAS environment to use
                        (default: prod)
```

### `gpas status`

Check the processing status of an uploaded batch by passing either a `mapping_csv` generated at upload time, or a comma-separated list of sample guids.

```shell
gpas status --mapping-csv example_mapping.csv --environment dev token.json
gpas status --guids 6e024eb1-432c-4b1b-8f57-3911fe87555f --format json token.json
```

```
% gpas status -h
usage: gpas status [-h] [--mapping-csv MAPPING_CSV] [--guids GUIDS] [--format {table,csv,json}] [--rename] [--raw]
                   [--environment {dev,staging,prod}]
                   token

Check the status of samples submitted to the GPAS platform

positional arguments:
  token                 Path of auth token available from GPAS Portal

options:
  -h, --help            show this help message and exit
  --mapping-csv MAPPING_CSV
                        Path of mapping CSV generated at upload time
                        (default: None)
  --guids GUIDS         Comma-separated list of GPAS sample guids
                        (default: )
  --format {table,csv,json}
                        Output format
                        (default: table)
  --rename              Use local sample names (requires --mapping-csv)
                        (default: False)
  --raw                 Emit raw response
                        (default: False)
  --environment {dev,staging,prod}
                        GPAS environment to use
                        (default: prod)
```



## Development and testing

Use pre-commit to apply black style at commit time (should happen automatically)

```
conda create -n gpas-cli-dev python=3.10 read-it-and-keep=0.3.0 samtools=1.15.1 pytest pytest-cov black pre-commit mypy
conda activate gpas-cli-dev
git clone https://github.com/GlobalPathogenAnalysisService/gpas-cli
cd gpas-cli
pip install -e ./

# Offline unit tests
pytest tests/test_gpas.py

# Online and upload tests require a valid token
pytest --cov=gpas
```
