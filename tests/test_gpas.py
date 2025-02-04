import subprocess
from pathlib import Path

import pytest
from gpas import data_dir, lib, misc, validation

data_dir = "tests/test-data"


def run(cmd, cwd="./"):  # Helper for CLI testing
    return subprocess.run(
        cmd, cwd=data_dir, shell=True, check=True, text=True, capture_output=True
    )


def test_version():
    run_cmd = run("gpas --version")


def test_static_assets_exist():
    validation.parse_countries_subdivisions()
    assert misc.get_reference_path("SARS-CoV-2").exists()


def test_validate_ok():
    df, schema_name = validation.validate(
        Path(data_dir) / Path("large-illumina-fastq.csv")
    )
    message = validation.build_validation_message(df, schema_name)
    # assert message == {
    #     "validation": {
    #         "status": "success",
    #         # "schema": "PairedFastqSchema",
    #         "samples": [
    #             {
    #                 "sample_name": "cDNA-VOC-1-v4-1",
    #                 "files": [
    #                     "/Users/bede/Research/Git/gpas-cli/tests/test-data/reads/large-illumina-fastq_1.fastq.gz",
    #                     "/Users/bede/Research/Git/gpas-cli/tests/test-data/reads/large-illumina-fastq_2.fastq.gz",
    #                 ],
    #             }
    #         ],
    #     }
    # }
    # Can't do above because of absolute paths
    assert message["validation"]["status"] == "success"
    assert message["validation"]["samples"][0]["sample_name"] == "cDNA-VOC-1-v4-1"
    assert message["validation"]["samples"][0]["files"][0].endswith(
        "reads/large-illumina-fastq_1.fastq.gz"
    )
    assert message["validation"]["samples"][0]["files"][1].endswith(
        "reads/large-illumina-fastq_2.fastq.gz"
    )


def test_validate_new_cli():
    run_cmd = run("gpas validate broken/impossible-country-region.csv --json-messages")
    assert (
        """{
    "validation": {
        "status": "failure",
        "errors": [
            {
                "error": "One or more regions are not valid ISO-3166-2 subdivisions for the specified country"
            }
        ]
    }
}"""
        in run_cmd.stdout
    )
    assert run_cmd.stdout != 0


def test_validate_fail_no_tags():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("no-tags.csv")
        )
    assert e.value.errors == [
        {"sample_name": "cDNA-VOC-1-v4-1", "error": "tags cannot be empty"}
    ]
    assert e.value.report == {
        "validation": {
            "status": "failure",
            "errors": [
                {"sample_name": "cDNA-VOC-1-v4-1", "error": "tags cannot be empty"}
            ],
        }
    }


def test_validate_fail_no_tags_colon():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("no-tags-colon.csv")
        )
    assert e.value.errors == [
        {"sample_name": "cDNA-VOC-1-v4-1", "error": "tags cannot be empty"}
    ]


def test_validate_fail_dupe_tags():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("dupe-tags.csv")
        )
    assert e.value.errors == [
        {"sample_name": "cDNA-VOC-1-v4-1", "error": "tags cannot be repeated"}
    ]


def test_validate_wrong_tags():
    """Should pass since tag validation does not happen offline"""
    run_cmd = run(f"gpas validate broken/wrong-tags.csv")
    assert "Validation successful" in run_cmd.stderr
    assert run_cmd.stdout == ""


def test_validate_fail_missing_files():
    # valid, schema, message = validation.validate(
    #     Path(data_dir) / Path("broken") / Path("broken-path.csv")
    # )
    # assert not valid and message == {
    #     "validation": {
    #         "status": "failure",
    #         "schema": "PairedFastqSchema",
    #         "errors": [
    #             {
    #                 "sample_name": "cDNA-VOC-1-v4-1",
    #                 "error": "fastq1 file does not exist",
    #             },
    #             {
    #                 "sample_name": "cDNA-VOC-1-v4-1",
    #                 "error": "fastq2 file does not exist",
    #             },
    #         ],
    #     }
    # }
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("broken-path.csv")
        )
    assert e.value.errors == [
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "fastq1 file does not exist",
        },
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "fastq2 file does not exist",
        },
    ]


def test_validate_fail_different_platforms():
    # valid, schema, message = validation.validate(
    #     Path(data_dir) / Path("broken") / Path("different-platforms.csv")
    # )
    # assert not valid and message == {
    #     "validation": {
    #         "status": "failure",
    #         "schema": "PairedFastqSchema",
    #         "errors": [
    #             {
    #                 "error": "instrument_platform must be the same for all samples in a submission"
    #             }
    #         ],
    #     }
    # }
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("different-platforms.csv")
        )
    assert e.value.errors == [
        {
            "error": "instrument_platform must be the same for all samples in a submission"
        }
    ]


def test_validate_fail_country_region():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("invalid-country-region.csv")
        )
    assert e.value.errors == [
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "Bretagn is not a valid ISO-3166-2 subdivision name",
        },
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "US is not a valid ISO-3166-1 alpha-3 country code",
        },
        {
            "error": "One or more regions are not valid ISO-3166-2 subdivisions for the specified country"
        },
    ]


def test_validate_empty_region():
    """Empty region should be fine"""
    _, message = validation.validate(Path(data_dir) / Path("empty-region.csv"))


def test_validate_empty_district():
    """Empty district should be fine"""
    _, message = validation.validate(Path(data_dir) / Path("empty-district.csv"))


def test_validate_empty_district_multiple():
    """Checking that a numerical district is correctly cast to a string by read_csv dtype"""
    _, message = validation.validate(
        Path(data_dir) / Path("empty-district-multiple.csv")
    )


def test_validate_fail_select_schema():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("no-schema.csv")
        )
    assert e.value.errors == [
        {
            "error": "could not infer schema from available columns. For FASTQ use 'fastq', for paired-end FASTQ use 'fastq1' and 'fastq2', and for BAM use 'bam'"
        }
    ]


def test_validate_fail_wrong_instrument():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("wrong-instrument.csv")
        )
    assert (
        e.value.errors[0]["error"]
        == "instrument_platform can only contain one of ['Illumina', 'Nanopore']"
    )


def test_validate_fail_path_suffix_instrument():
    """Check that multiple errors are caught in one go (laziness)"""
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("bad-path-suffix-instrument.csv")
        )
    # assert e.value.errors[0]["error"].startswith(
    #     "instrument_platform value 'Illuminati' is not in set"
    # )
    # assert e.value.errors[1]["error"] == "fastq1 file does not exist"
    # assert (
    #     e.value.errors[2]["error"]
    #     == "fastq2 must end with .fastq.gz or .bam as appropriate"
    # )
    # assert e.value.errors[3]["error"] == "fastq2 file does not exist"

    print(e.value.errors)

    assert e.value.errors == [
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "fastq1 file does not exist",
        },
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "fastq2 file does not exist",
        },
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "fastq2 must end with .fastq.gz or .bam as appropriate",
        },
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "instrument_platform can only contain one of ['Illumina', 'Nanopore']",
        },
    ]


def test_validate_nullable_batch():
    _, message = validation.validate(Path(data_dir) / Path("empty-batch-run.csv"))


def test_validate_nullable_run():
    _, message = validation.validate(Path(data_dir) / Path("empty-run.csv"))


def test_decontamination():
    lib.Batch(Path(data_dir) / Path("large-nanopore-fastq.csv"))._decontaminate()
    lib.Batch(Path(data_dir) / Path("large-illumina-fastq.csv"))._decontaminate()
    lib.Batch(Path(data_dir) / Path("large-nanopore-bam.csv"))._decontaminate()
    lib.Batch(Path(data_dir) / Path("large-illumina-bam.csv"))._decontaminate()


def test_decontamination_stats():
    stdout = """Input reads file 1	5034
Input reads file 2	5034
Kept reads 1	5006
Kept reads 2	5006

"""
    assert lib.parse_decontamination_stats(stdout) == {
        "in": 10068,
        "out": 10012,
        "fraction": 0.0056,
    }


def test_upload_no_token_save_reads():
    """When run without a token, upload should quit after decontamination"""
    run_cmd = run("gpas upload large-nanopore-fastq.csv --save-reads")
    assert "Saved decontaminated reads" in run_cmd.stderr
    assert (
        data_dir
        / Path("decontaminated-reads")
        / Path("COVID_locost_2_barcode10.reads.fastq.gz")
    ).exists()
    run("rm -rf decontaminated-reads")


def test_weird_illumina_suffix():
    run("cp reads/large-illumina-fastq_1.fastq.gz reads/foo.fastq.gz")
    run("cp reads/large-illumina-fastq_2.fastq.gz reads/baa.fastq.gz")
    _, message = validation.validate(Path(data_dir) / Path("weird-illumina-suffix.csv"))
    run("rm reads/foo.fastq.gz reads/baa.fastq.gz")


def test_fail_no_header_cli():
    """Generic uncaught exception"""
    run_cmd = run("gpas validate broken/no-header.csv --json-messages")
    assert "unsupported operand type(s) for -: 'str' and 'int'" in run_cmd.stdout


def test_space_mapping_csv_reads():
    """Spaces should be tolerated in mapping CSV and reads filenames"""
    df, schema_name = validation.validate(
        Path(data_dir) / Path("large-nanopore-fastq space.csv")
    )


def test_validate_fail_fastq_empty():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("empty-fastq.csv")
        )
    assert e.value.errors[0]["error"] == "fastq cannot be empty"


def test_validate_fail_bam_empty():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("empty-bam.csv")
        )
    assert e.value.errors[0]["error"] == "bam cannot be empty"


def test_validate_fail_completely_empty():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("empty.csv")
        )
    assert (
        e.value.errors[0]["error"]
        == "failed to parse upload CSV (No columns to parse from file)"
    )


def test_validate_fail_invalid_date():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("invalid-date.csv")
        )
    assert e.value.errors[0] == {
        "sample_name": "COVID_locost_2_barcode10",
        "error": "fastq file does not exist",
    }
    assert e.value.errors[1]["error"].startswith(
        "collection_date must be in format YYYY-MM-DD between 2019-01-01"
    )


def test_validate_fail_non_iso_date():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("non-iso-date.csv")
        )
    assert (
        "collection_date must be in format YYYY-MM-DD between 2019-01-01"
        in e.value.errors[0]["error"]
    )


def test_validate_fail_empty_date():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("empty-date.csv")
        )

    assert e.value.errors[0]["error"] == "collection_date cannot be empty"
    assert e.value.errors[1] == {
        "sample_name": "COVID_locost_2_barcode10",
        "error": "fastq file does not exist",
    }
    assert (
        "collection_date must be in format YYYY-MM-DD between 2019-01-01"
        in e.value.errors[2]["error"]
    )


def test_validate_fail_insane_date():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("insane-date.csv")
        )
    assert e.value.errors[0] == {
        "sample_name": "COVID_locost_2_barcode10",
        "error": "fastq file does not exist",
    }
    assert (
        "collection_date must be in format YYYY-MM-DD between 2019-01-01"
        in e.value.errors[1]["error"]
    )


def test_validate_fail_dupe_fastqs():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("dupe-fastqs.csv")
        )
    assert e.value.errors[0] == {
        "sample_name": "COVID_locost_2_barcode10_x",
        "error": "fastq must be unique",
    }


def test_validate_fail_dupe_names():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("dupe-names.csv")
        )
    assert e.value.errors[0] == {
        "sample_name": 1,
        "error": "sample_name must be unique",
    }


def test_validate_fail_empty_name():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("empty-name.csv")
        )
    assert e.value.errors[0] == {"error": "sample_name cannot be empty"}


def test_validate_cayman():
    _, message = validation.validate(Path(data_dir) / Path("cayman.csv"))


def test_validate_vietnam():
    _, message = validation.validate(Path(data_dir) / Path("vietnam.csv"))


def test_validate_guyana():
    """Contains a space in the region field"""
    _, message = validation.validate(Path(data_dir) / Path("guyana.csv"))


def test_validate_no_run_batch():
    _, message = validation.validate(
        Path(data_dir) / Path("large-nanopore-fastq-no-run-batch.csv")
    )


def test_validate_negative():
    _, message = validation.validate(Path(data_dir) / Path("negative-control.csv"))


def test_validate_fail_not_unicode():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("not-unicode.csv")
        )
    assert "failed to parse upload CSV" in e.value.errors[0]["error"]


def test_paired_bam_first_read_not_equal():
    """Ensure that R1 is not the same as R2"""
    run_cmd = run("gpas upload large-illumina-bam.csv --working-dir ./")
    r1_str = run("zcat < cDNA-VOC-1-v4-1_1.fastq.gz | head -n 2 | tail -n 1").stdout
    r2_str = run("zcat < cDNA-VOC-1-v4-1_2.fastq.gz | head -n 2 | tail -n 1").stdout
    assert r1_str != r2_str
    run(
        "rm cDNA-VOC-1-v4-1_1.fastq.gz cDNA-VOC-1-v4-1_2.fastq.gz cDNA-VOC-1-v4-1.reads_1.fastq.gz cDNA-VOC-1-v4-1.reads_2.fastq.gz"
    )


def test_validate_fail_illegal_chars():
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("illegal-chârs.csv")
        )
    assert e.value.errors[0] == {
        "error": "upload csv path contains illegal characters",
    }


def test_validate_fail_dupe_fastqs_illumina():
    """Two records, one with duplicate fastq1"""
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("dupe-fastqs-illumina.csv")
        )
    assert e.value.errors == [
        {
            "sample_name": "cDNA-VOC-1-v4-1",
            "error": "fastq1 and fastq2 must be jointly unique",
        },
        {
            "sample_name": "cDNA-VOC-1-v4-2",
            "error": "fastq1 and fastq2 must be jointly unique",
        },
        {"sample_name": "cDNA-VOC-1-v4-2", "error": "fastq1 must be unique"},
        {"sample_name": "cDNA-VOC-1-v4-2", "error": "fastq2 must be unique"},
    ]


# This is a valid test that is currently failing
def test_validate_fail_dupe_fastq1_fastq2_illumina():
    """Two records, one with duplicate fastq1"""
    with pytest.raises(validation.ValidationError) as e:
        _, message = validation.validate(
            Path(data_dir) / Path("broken") / Path("dupe-fastq1-fastq2.csv")
        )
    assert e.value.errors[0] == {
        "sample_name": "cDNA-VOC-1-v4-1",
        "error": "fastq1 and fastq2 cannot be the same",
    }
