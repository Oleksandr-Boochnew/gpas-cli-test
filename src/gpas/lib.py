from subprocess import CalledProcessError
import sys
import gzip
import json
import asyncio
import logging
import datetime

from typing import Any
from pathlib import Path
from functools import partial

import tqdm
import httpx
import requests

import pandas as pd

from tqdm.contrib.logging import logging_redirect_tqdm

from gpas import misc, data_dir
from gpas.validation import validate
from gpas.misc import (
    run,
    ENVIRONMENTS,
    DEFAULT_ENVIRONMENT,
    FILE_TYPES,
    ENDPOINTS,
    GOOD_STATUSES,
)


class DecontaminationError(Exception):
    pass


def fetch_user_details(access_token, environment: ENVIRONMENTS):
    """Test API authentication and return user details, otherwise exit"""
    endpoint = (
        ENDPOINTS[environment.value]["HOST"]
        + ENDPOINTS[environment.value]["ORDS_PATH"]
        + "userOrgDtls"
    )
    try:
        logging.info(f"Fetching user details {endpoint=}")
        r = requests.get(endpoint, headers={"Authorization": f"Bearer {access_token}"})
        if not r.ok:
            r.raise_for_status()
        result = r.json().get("userOrgDtl", {})[0]
        logging.info(f"{result=}")
        user = result.get("userName")
        organisation = result.get("organisation")
        permitted_tags = result.get("tags", {})[0].keys()
    except requests.exceptions.RequestException as e:
        logging.error(str(e))
        sys.exit(1)
    return user, organisation, permitted_tags


def parse_token(token: Path) -> dict:
    return json.loads(token.read_text())


def parse_mapping(mapping_csv: Path = None) -> pd.DataFrame:
    df = pd.read_csv(mapping_csv)
    expected_columns = {
        "local_batch",
        "local_run_number",
        "local_sample_name",
        "gpas_batch",
        "gpas_run_number",
        "gpas_sample_name",
    }
    if not expected_columns.issubset(set(df.columns)):
        raise RuntimeError(f"One or more expected columns missing from mapping CSV")
    return df


def update_fasta_header(path: Path, guid: str, name: str):
    """Update the header line of a gzipped fasta file in place"""
    with gzip.open(path, "rt") as fh:
        contents = fh.read()
    if guid in contents:
        with gzip.open(path, "wt") as fh:
            fh.write(contents.replace(guid, f"{guid}|{name}"))
    else:
        logging.warning(f"Could not rename {guid} inside {name}.fasta.gz")


async def fetch_status_async(
    access_token: str,
    mapping_csv: Path = None,
    guids: list[str] = [],
    environment: ENVIRONMENTS = DEFAULT_ENVIRONMENT,
    rename: bool = False,
    raw: bool = False,
) -> list[dict]:
    """Returns a list of dicts of containing status records"""
    fetch_user_details(access_token, environment)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    endpoint = (
        ENDPOINTS[environment.value]["HOST"]
        + ENDPOINTS[environment.value]["API_PATH"]
        + "get_sample_detail"
    )

    if mapping_csv:
        logging.info(f"Using samples in {mapping_csv}")
        mapping_df = parse_mapping(mapping_csv)
        guids = mapping_df["gpas_sample_name"].tolist()
    elif guids:
        logging.info(f"Using list of guids")
    else:
        raise RuntimeError("Neither a mapping csv nor guids were specified")

    limits = httpx.Limits(
        max_keepalive_connections=10, max_connections=20, keepalive_expiry=10
    )
    transport = httpx.AsyncHTTPTransport(limits=limits, retries=5)
    async with httpx.AsyncClient(transport=transport, timeout=30) as client:
        guids_urls = {guid: f"{endpoint}/{guid}" for guid in guids}
        tasks = [
            fetch_status_single_async(client, guid, url, headers)
            for guid, url in guids_urls.items()
        ]
        records = [
            await f
            for f in tqdm.tqdm(
                asyncio.as_completed(tasks),
                desc=f"Querying status for {len(guids)} samples",
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
                total=len(tasks),
            )
        ]

    if rename:
        if mapping_csv and "local_sample_name" in mapping_df.columns:
            guids_names = mapping_df.set_index("gpas_sample_name")[
                "local_sample_name"
            ].to_dict()
            records = pd.DataFrame(records).replace(guids_names).to_dict("records")
        else:
            logging.warning(
                "Samples were not renamed because a valid mapping csv was not specified"
            )

    return records


async def fetch_status_single_async(client, guid, url, headers, n_retries=5):
    # for i in range(n_retries):
    #     try:
    #         r = await client.get(url=url, headers=headers)
    #         if r.status_code == httpx.codes.ok:
    #             r_json = r.json()[0]
    #             status = r_json.get("status")
    #             result = dict(sample=guid, status=status)
    #             if status not in GOOD_STATUSES:
    #                 logging.warning(f"Skipping {guid} (status {status})")
    #         else:
    #             result = dict(sample=guid, status="Unknown")
    #             logging.warning(f"Retrying (attempt {i+1})")  # Failed, retry
    #     except httpx.TransportError as e:
    #         logging.warning(f"Transport error, retrying (attempt {i+1})")  # Failed, retry
    #         if i == n_retries - 1:
    #             logging.warning("Giving up")
    #             raise  # Persisted after all retries, so throw it, don't proceed
    #         # Otherwise retry, connection was terminated due to httpx bug
    #     else:
    #         break  # exit the for loop if it succeeds
    # return result
    r = await client.get(url=url, headers=headers)
    if r.status_code == httpx.codes.ok:
        r_json = r.json()[0]
        status = r_json.get("status")
        result = dict(sample=guid, status=status)
        if status not in GOOD_STATUSES:
            with logging_redirect_tqdm():
                logging.info(f"Skipping {guid} (status {status})")
    else:
        result = dict(sample=guid, status="Unknown")
        with logging_redirect_tqdm():
            logging.warning(f"HTTP {r.status_code} ({guid})")
        if r.status_code == 401:
            raise RuntimeError(
                f"Authorisation failed (HTTP {r.status_code}). Invalid token?"
            )
    return result


async def download_async(
    guids: list,
    file_types: list[str],
    access_token: str,
    environment: ENVIRONMENTS = DEFAULT_ENVIRONMENT,
    out_dir: Path = Path.cwd(),
    guids_names=None,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    endpoint = (
        ENDPOINTS[environment.value]["HOST"]
        + ENDPOINTS[environment.value]["API_PATH"]
        + "get_output"
    )
    unrecognised_file_types = set(file_types) - {t.name for t in FILE_TYPES}
    if unrecognised_file_types:
        raise RuntimeError(f"Invalid file type(s): {unrecognised_file_types}")
    logging.info(f"Fetching file types {file_types}")

    limits = httpx.Limits(
        max_keepalive_connections=10, max_connections=20, keepalive_expiry=10
    )
    transport = httpx.AsyncHTTPTransport(limits=limits, retries=5)
    async with httpx.AsyncClient(transport=transport, timeout=120) as client:
        guids_types_urls = {}
        for guid in guids:
            for file_type in file_types:
                guids_types_urls[(guid, file_type)] = f"{endpoint}/{guid}/{file_type}"
        tasks = [
            download_single_async(
                client,
                guid,
                file_type,
                url,
                headers,
                out_dir,
                guids_names[guid] if guids_names else None,
            )
            for (guid, file_type), url in guids_types_urls.items()
        ]
        return [
            await f
            for f in tqdm.tqdm(
                asyncio.as_completed(tasks),
                desc=f"Downloading {len(tasks)} files for {len(guids)} samples",
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
                total=len(tasks),
            )
        ]


async def download_single_async(
    client, guid, file_type, url, headers, out_dir, name=None, retries=5
):
    file_types_extensions = {
        "json": "json",
        "fasta": "fasta.gz",
        "bam": "bam",
        "vcf": "vcf",
    }
    prefix = name if name else guid

    # for i in range(retries):
    #     try:
    #         r = await client.get(url=url, headers=headers)
    #         if r.status_code == httpx.codes.ok:
    #             with open(
    #                 Path(out_dir)
    #                 / Path(f"{prefix}.{file_types_extensions[file_type]}"),
    #                 "wb",
    #             ) as fh:
    #                 fh.write(r.content)
    #             if name and file_type == "fasta":
    #                 update_fasta_header(
    #                     Path(f"{prefix}.{file_types_extensions[file_type]}"), guid, name
    #                 )
    #         else:
    #             time.sleep(1)
    #             print('Sleeping')
    #             logging.warning(f"Retrying (attempt {i+1})")  # Failed, retry
    #     except ssl.SSLWantReadError as e:
    #         logging.warning(f"Transport error, retrying (attempt {i+1})")  # Failed, retry
    #         if i == n_retries - 1:
    #             logging.warning("Giving up")
    #             raise  # Persisted after all retries, so throw it, don't proceed
    #         # Otherwise retry, connection was terminated due to httpx bug
    #     else:
    #         break  # exit the for loop if it succeeds

    prefix = name if name else guid
    r = await client.get(url=url, headers=headers)
    if r.status_code == httpx.codes.ok:
        print(Path(out_dir) / Path(f"{prefix}.{file_types_extensions[file_type]}"))
        with open(
            Path(out_dir) / Path(f"{prefix}.{file_types_extensions[file_type]}"), "wb"
        ) as fh:
            fh.write(r.content)
        if name and file_type == "fasta":
            update_fasta_header(
                Path(f"{prefix}.{file_types_extensions[file_type]}"), guid, name
            )
    else:
        result = dict(sample=guid, status="Unknown")
        with logging_redirect_tqdm():
            logging.warning(f"Skipping {guid}.{file_type} (HTTP {r.status_code})")


def fetch_status(
    access_token: str,
    mapping_csv: Path = None,
    guids: list[str] = [],
    environment: ENVIRONMENTS = DEFAULT_ENVIRONMENT,
    rename: bool = False,
    raw: bool = False,
) -> list[dict]:
    """Returns a list of dicts of containing status records"""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    endpoint = (
        ENDPOINTS[environment.value]["HOST"]
        + ENDPOINTS[environment.value]["API_PATH"]
        + "get_sample_detail/"
    )
    """
    Return a list of dictionaries given a list of guids
    """

    if mapping_csv:
        logging.info(f"Using samples in {mapping_csv}")
        mapping_df = parse_mapping(mapping_csv)
        guids = mapping_df["gpas_sample_name"].tolist()
    elif guids:
        logging.info(f"Using list of guids")
    else:
        raise RuntimeError("Neither a mapping csv nor guids were specified")

    records = []
    for guid in tqdm.tqdm(guids):
        r = requests.get(url=endpoint + guid, headers=headers)
        if r.ok:
            if raw:
                records.append(r.json())
            else:
                records.append(
                    dict(
                        sample=r.json()[0].get("name"), status=r.json()[0].get("status")
                    )
                )
        else:
            records.append(dict(sample=guid, status="Unknown"))
            logging.warning(f"{guid} (error {r.status_code})")

    if rename:
        if mapping_csv and "local_sample_name" in mapping_df.columns:
            guids_names = mapping_df.set_index("gpas_sample_name")[
                "local_sample_name"
            ].to_dict()
            records = pd.DataFrame(records).replace(guids_names).to_dict("records")
        else:
            logging.warning(
                "Samples were not renamed because a valid mapping csv was not specified"
            )

    return records


class Sample:
    """
    Represent a single sample
    """

    def __init__(
        self,
        batch,
        run_number,
        sample_name,
        control,
        collection_date,
        tags,
        country,
        region,
        specimen_organism,
        host,
        instrument_platform,
        primer_scheme,
        schema_name,
        working_dir,
        district=None,
        fastq=None,
        fastq1=None,
        fastq2=None,
        bam=None,
    ):
        self.batch = batch
        self.run_number = run_number
        self.sample_name = sample_name
        self.fastq = fastq
        self.fastq1 = fastq1
        self.fastq2 = fastq2
        self.bam = bam
        self.control = control
        self.collection_date = collection_date
        self.tags = tags.split(":")
        self.country = country
        self.region = region
        self.district = district
        self.specimen_organism = specimen_organism
        self.host = host
        self.instrument_platform = instrument_platform
        self.primer_scheme = primer_scheme
        self.schema_name = schema_name
        self.paired = True if self.schema_name.startswith("Paired") else False
        self.ref_path = self.get_reference_path()
        self.working_dir = working_dir
        self.uploaded = False

    def get_reference_path(self):
        prefix = data_dir / Path("refs")
        organisms_paths = {"SARS-CoV-2": "MN908947_no_polyA.fasta"}
        return prefix / organisms_paths[self.specimen_organism]

    def decontaminate(self):
        if "Bam" in self.schema_name:  # Preprocess BAMs into FASTQs
            self._convert_bam(paired=self.paired)
        self._read_it_and_keep()
        logging.info(f"{self.decontamination_stats=}")

    def _convert_bam(self, paired=False):
        prefix = Path(self.working_dir) / Path(self.sample_name)
        if not self.paired:
            cmd_run = run(
                f"samtools fastq -0 {prefix.with_suffix('.fastq.gz')} {self.bam}"
            )
            self.fastq = self.working_dir / Path(self.sample_name + ".fastq.gz")
        else:
            cmd_run = run(
                f"samtools sort {self.bam} | samtools fastq -N -1 {self.working_dir / Path(self.sample_name + '_1.fastq.gz')} -2 {self.working_dir /  Path(self.sample_name + '_2.fastq.gz')}"
            )
            self.fastq1 = self.working_dir / Path(self.sample_name + "_1.fastq.gz")
            self.fastq2 = self.working_dir / Path(self.sample_name + "_2.fastq.gz")
        logging.info([cmd_run.returncode, cmd_run.args, cmd_run.stdout])

    def _read_it_and_keep(self):
        # prefix = Path(self.working_dir) / Path(str(reads1).removesuffix(".fastq.gz"))
        if not self.fastq2:
            cmd = f"readItAndKeep --tech ont --enumerate_names --ref_fasta {self.ref_path} --reads1 {self.fastq} --outprefix {self.working_dir / self.sample_name}"
        else:
            cmd = f"readItAndKeep --tech illumina --enumerate_names --ref_fasta {self.ref_path} --reads1 {self.fastq1} --reads2 {self.fastq2} --outprefix {self.working_dir / self.sample_name}"

        try:
            cmd_run = run(cmd)
        except CalledProcessError as e:
            raise DecontaminationError(f"Decontamination failed for {self.sample_name}")

        self.riak_fastq = (
            self.working_dir / Path(self.sample_name + ".reads.fastq.gz")
            if self.fastq
            else None
        )
        self.riak_fastq1 = (
            self.working_dir / Path(self.sample_name + ".reads_1.fastq.gz")
            if self.fastq1
            else None
        )
        self.riak_fastq2 = (
            self.working_dir / Path(self.sample_name + ".reads_2.fastq.gz")
            if self.fastq2
            else None
        )

        # logging.warning([cmd_run.returncode, cmd_run.args, cmd_run.stdout])
        self.decontamination_stats = parse_decontamination_stats(cmd_run.stdout)

    def _hash_fastq(self):
        self.md5 = misc.hash_file(str(self.fastq))

    def _hash_fastqs(self):
        self.md5_1 = misc.hash_file(str(self.fastq1))
        self.md5_2 = misc.hash_file(str(self.fastq2))

    def _upload_reads(self, batch_url, headers):
        """Upload an unpaired FASTQ file to the Organisation's input bucket in OCI"""
        url_prefix = batch_url + self.guid
        if not self.uploaded:
            if not self.paired:
                with open(self.riak_fastq, "rb") as fh:
                    r = requests.put(
                        url=f"{url_prefix}.reads.fastq.gz", data=fh, headers=headers
                    )
            else:
                with open(self.riak_fastq1, "rb") as fh:
                    r = requests.put(
                        f"{url_prefix}.reads_1.fastq.gz", data=fh, headers=headers
                    )
                with open(self.riak_fastq2, "rb") as fh:
                    r = requests.put(
                        f"{url_prefix}.reads_2.fastq.gz", data=fh, headers=headers
                    )
        self.uploaded = True


class Batch:
    """
    Represent a batch of samples
    """

    def __init__(
        self,
        upload_csv: Path,
        token: Path = None,
        environment: ENVIRONMENTS = DEFAULT_ENVIRONMENT,
        working_dir: Path = Path("/tmp"),
        threads: int = 0,
    ):
        self.upload_csv = upload_csv
        self.token = parse_token(token) if token else None
        self.environment = environment
        self.working_dir = working_dir
        self.threads = threads
        self.json = {"validation": "", "decontamination": "", "submission": ""}
        self.df, self.validation_report = validate(upload_csv)
        self.schema_name = self.df.pandera.schema.name
        self.errors = {"decontamination": [], "submission": []}
        batch_attrs = {
            "schema_name": self.schema_name,
            "working_dir": self.working_dir,
        }
        self.samples = [
            Sample(**r, **batch_attrs)
            for r in self.df.fillna("").reset_index().to_dict("records")
        ]
        self.paired = self.samples[0].paired
        if self.token:
            (
                self.user,
                self.organisation,
                self.permitted_tags,
            ) = self._fetch_user_details()
            self.headers = {
                "Authorization": f"Bearer {self.token['access_token']}",
                "Content-Type": "application/json",
            }
            self.upload_headers = {k: v for k, v in self.headers.items()}
            self.upload_headers["Content-Type"] = "application/octet-stream"

        currentTime = (
            datetime.datetime.now(datetime.timezone.utc)
            .astimezone()
            .isoformat(timespec="milliseconds")
        )
        tzStartIndex = len(currentTime) - 6
        self.uploaded_on = currentTime[:tzStartIndex] + "Z" + currentTime[tzStartIndex:]

    def _fetch_user_details(self):
        return fetch_user_details(self.token["access_token"], self.environment)

    def _decontaminate(self):
        list(map(lambda s: s.decontaminate(), self.samples))

    def _hash_fastqs(self):
        if not self.paired:
            list(map(lambda s: s._hash_fastq(), self.samples))
        else:
            list(map(lambda s: s._hash_fastqs(), self.samples))

    def _get_sample_attrs(self, attr) -> dict[str, Any]:
        return {s.sample_name: getattr(s, attr) for s in self.samples}

    def _set_samples(self, name, value):
        map(partial(setattr, name, value), self.samples)

    def _fetch_guids(self):
        md5_attr = "md5" if not self.paired else "md5_1"
        checksums = list(self._get_sample_attrs(md5_attr).values())
        payload = {
            "batch": {
                "organisation": self.organisation,
                "uploadedOn": self.uploaded_on,
                "uploadedBy": self.user,
                "samples": checksums,
            }
        }
        endpoint = (
            ENDPOINTS[self.environment.value]["HOST"]
            + ENDPOINTS[self.environment.value]["ORDS_PATH"]
            + "createSampleGuids"
        )
        logging.info(f"Fetching guids {endpoint=}")
        r = requests.post(url=endpoint, data=json.dumps(payload), headers=self.headers)
        if not r.ok:
            r.raise_for_status()
        result = r.json()
        logging.info(f"{result=}")
        self.batch_guid = result["batch"]["guid"]
        hashes_guids = {s["hash"]: s["guid"] for s in result["batch"]["samples"]}
        for sample in self.samples:
            sample.guid = hashes_guids[getattr(sample, md5_attr)]

    def _rename_fastqs(self):
        """Rename decontaminated fastqs using server-side guids"""
        for s in self.samples:
            s.riak_fastq = (
                s.riak_fastq.rename(s.working_dir / Path(s.guid + ".fastq.gz"))
                if s.riak_fastq
                else None
            )
            s.riak_fastq1 = (
                s.riak_fastq1.rename(s.working_dir / Path(s.guid + ".fastq.gz"))
                if s.riak_fastq1
                else None
            )
            s.riak_fastq2 = (
                s.riak_fastq2.rename(s.working_dir / Path(s.guid + ".fastq.gz"))
                if s.riak_fastq2
                else None
            )
            # print(type(s.fastq))
            # print(s.fastq)
            # print(s.riak_fastq)

    def _fetch_par(self):
        """Private method that calls ORDS to get a Pre-Authenticated Request.

        The PAR url is used to upload data to the Organisation's input bucket in OCI

        Returns
        -------
        par: str
        """
        endpoint = (
            ENDPOINTS[self.environment.value]["HOST"]
            + ENDPOINTS[self.environment.value]["ORDS_PATH"]
            + "pars"
        )
        logging.info(f"Fetching PAR; {endpoint=} {self.headers=}")
        r = requests.get(url=endpoint, headers=self.headers)
        if not r.ok:
            r.raise_for_status()
        result = json.loads(r.content)
        logging.info(f"{result=}")

        if result.get("status") == "error":
            raise RuntimeError("Problem fetching PAR")
        print(result, r.status_code)
        self.par = result["par"]
        self.bucket = self.par.split("/")[-3]
        self.batch_url = self.par + self.batch_guid + "/"

    def _upload_samples(self):
        # list(map(lambda s: s._upload_reads(), self.samples))
        for s in self.samples:
            s._upload_reads(self.batch_url, self.upload_headers)

    def _submit(self):
        self._set_samples("uploaded", False)
        self._fetch_par()
        if not self.errors["decontamination"]:
            self._build_submission()
            print(json.dumps(self.submission, indent=4))
            self._upload_samples()
            for s in self.samples:
                print("Uploaded", s.sample_name)
        endpoint = (
            ENDPOINTS[self.environment.value]["HOST"]
            + ENDPOINTS[self.environment.value]["ORDS_PATH"]
            + "batches"
        )
        r = requests.post(url=endpoint, json=self.submission, headers=self.headers)
        logging.INFO("POSTing JSON")
        logging.INFO(r.text)
        if not r.ok:
            self.errors["submission"].append(
                {"error": "Sending metadata JSON to ORDS failed"}
            )
        else:  # Make the finalisation mark
            url = self.par + self.batch_guid + "/upload_done.txt"
            r = requests.put(url=url, headers=self.upload_headers)
            logging.INFO("PUTting upload_done.txt")
            if not r.ok:
                self.errors["submission"].append(
                    {"error": "Sending metadata JSON to ORDS failed"}
                )

    def _build_submission(self):
        """Prepare the JSON payload for the GPAS Upload app

        Returns
        -------
            dict : JSON payload to pass to GPAS Electron upload app via STDOUT
        """
        # self.sample_sheet = copy.deepcopy(self.df[['batch', 'run_number', 'sample_name', 'gpas_batch', 'gpas_run_number', 'gpas_sample_name']])
        # self.sample_sheet.rename(columns={'batch': 'local_batch', 'run_number': 'local_run_number', 'sample_name': 'local_sample_name'}, inplace=True)
        # self.df.set_index('gpas_sample_name', inplace=True)

        samples = []
        for s in self.samples:
            sample = {
                "name": s.guid,
                # "run_number": row.gpas_run_number,
                "tags": s.tags,
                "control": s.control,
                "collection_date": str(s.collection_date.date()),
                "country": s.country,
                "region": s.region,
                "district": s.district,
                "specimen": s.specimen_organism,
                "host": s.host,
                "instrument": {"platform": s.instrument_platform},
                "primer_scheme": s.primer_scheme,
            }
            if self.paired:
                sample["pe_reads"] = {
                    "r1_uri": str(s.riak_fastq1),
                    "r1_md5": s.md5_1,
                    "r2_uri": str(s.riak_fastq2),
                    "r2_md5": s.md5_2,
                }
                logging.info(f"{s.riak_fastq1=}, {s.riak_fastq2=}")
            else:
                sample["se_reads"] = {"uri": str(s.riak_fastq), "md5": s.md5}
            samples.append(sample)

        self.submission = {
            "status": "completed",
            "batch": {
                "file_name": self.batch_guid,
                "bucket_name": self.bucket,
                "uploaded_on": str(self.uploaded_on),
                "uploaded_by": self.user,
                "organisation": self.organisation,
                # "run_numbers": [i for i in self.run_number_lookup.values()],
                "samples": samples,
            },
        }

    def upload(self):
        self._decontaminate()
        self._hash_fastqs()
        self._fetch_guids()
        self._rename_fastqs()
        self._submit()
        # for s in self.samples:
        # print(s.sample_name, s.decontamination_stats, s.md5, s.guid)
        # print(s.riak_fastq, s.riak_fastq1, s.riak_fastq2)
        # assert s.riak_fastq.exists() if s.riak_fastq else True
        # assert s.riak_fastq1.exists() if s.riak_fastq1 else True
        # assert s.riak_fastq2.exists() if s.riak_fastq2 else True

    # def _number_runs(self):
    #     run_number_lookup = {}
    #     # deal with case when they are all NaN
    #     if self.df.run_number.isna().all():
    #         run_number_lookup[''] = ''
    #     else:
    #         self.run_numbers = list(self.df.run_number.unique())
    #         gpas_run = 1
    #         for i in self.run_numbers:
    #             if pandas.notna(i):
    #                 run_number_lookup[i] = gpas_run
    #                 gpas_run += 1
    #    return run_number_lookup


def parse_decontamination_stats(stdout: str) -> dict:
    """
    Parse read-it-and-keep kept and discarded read counts
    """
    lines = stdout.strip().splitlines()
    counts = [int(l.rpartition("\t")[2]) for l in lines]
    count_in = counts[0] + counts[1]
    count_out = counts[2] + counts[3]
    delta = count_in - count_out
    assert delta >= 0
    return {
        "in": count_in,
        "out": count_out,
        "fraction": round(delta / count_in, 4),
    }
