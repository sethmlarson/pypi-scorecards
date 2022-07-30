import datetime
import json
import typing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import urllib3
from tqdm import tqdm

http = urllib3.PoolManager(retries=urllib3.util.Retry(total=10, backoff_factor=0.5))
thread_pool = ThreadPoolExecutor()
top_pypi_packages_url = "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/top-pypi-packages-30-days.min.json"
today = datetime.date.today().strftime("%Y-%m-%d")
base_dir = Path(__file__).absolute().parent


@dataclass
class Package:
    name: str
    downloads: int
    overall: float = 0.0
    checks: typing.Dict[str, typing.Optional[int]] = field(default_factory=dict)


packages: typing.Dict[str, Package] = {}


def fetch_checks_for_package(package_name):
    global packages
    resp = http.request("GET", f"https://deps.dev/_/s/pypi/p/{package_name}/v/")
    if resp.status != 200:
        return
    data = json.loads(resp.data)

    package = packages[package_name]
    try:
        for project in data["version"]["projects"]:
            if "scorecardV2" in project:
                scorecard = project["scorecardV2"]
                for check in scorecard["check"]:
                    check_name = check["name"]
                    check_score = check["score"]

                    # This is how deps.dev denotes a missing score in the API.
                    if check_score < 0:
                        continue
                    # The score is already set and larger than this project value.
                    if (package.checks.get(check_name, 0.0) or 0.0) > check_score:
                        continue

                    package.checks[check_name] = check_score

    except (KeyError, IndexError) as e:
        return


def fill_in_missing_checks(check_names):
    # Fills in the missing checks for every package
    # and calculates the overall score for the package.
    for package in packages.values():
        for check_name in check_names:
            package.checks.setdefault(check_name, None)
        package.overall = sum(
            check_value or 0.0 for check_value in package.checks.values()
        ) / len(package.checks)


def write_packages_to_csv(check_names):
    with (base_dir / "data" / f"{today}.csv").open("w") as f:
        f.truncate()

        f.write(f"Package,Downloads,Overall,{','.join(check_names)}\n")
        for package in sort_packages(packages):
            f.write(
                f"{package.name},{package.downloads},{package.overall:.2f},{','.join(check_value_or_dash(package.checks[check_name]) for check_name in check_names)}\n"
            )


def write_packages_to_readme(check_names):
    with (base_dir / "README.md").open("w") as f:
        f.truncate()
        f.write(
            f"""# OpenSSF Scorecards for top Python packages

Top 5,000 Python packages by downloads and their [OpenSSF Scorecard values](https://github.com/ossf/scorecard). Data gathered from [deps.dev public dataset](https://deps.dev) on {datetime.date.today().strftime('%b %-d, %Y')} and is updated weekly. Historical data can be found [under `data/`](https://github.com/sethmlarson/pypi-scorecards/tree/main/data). For more information about individual Scorecard checks you can [read the documentation](https://github.com/ossf/scorecard/blob/main/docs/checks.md). 

**NOTE:** All missing values are scored as a zero. deps.dev doesn't take missing values into account for their scoring of packages. This is the likely reason why you may see a difference in the value reported here versus the one on deps.dev for a package.

"""
        )

        f.write(f"Package|Downloads|Overall|{'|'.join(check_names)}\n")
        f.write(f"-{'|-' * (len(check_names) + 2)}\n")

        for i, package in enumerate(sort_packages(packages)):
            f.write(
                f"[{package.name}](https://pypi.org/project/{package.name})|{package.downloads:,}|[{package.overall:.2f}/10](https://deps.dev/pypi/{package.name})|{'|'.join(check_value_or_dash(package.checks[check_name], '–') for check_name in check_names)}\n"
            )
            # Limit to 1000 so we don't tear at the bottom of the README.
            # GitHub doesn't like gigantic READMEs apparently.
            if i == 999:
                break


def check_value_or_dash(check_value, empty=""):
    return str(check_value) if check_value is not None else empty


def sort_packages(packages):
    return sorted(packages.values(), key=lambda p: (-p.overall, -p.downloads, p.name))


def main():
    global packages

    # Fetch all scorecard values for top packages by downloads
    rows = json.loads(http.request("GET", top_pypi_packages_url).data)["rows"]
    packages = {
        row["project"]: Package(name=row["project"], downloads=row["download_count"])
        for row in rows
    }

    results = thread_pool.map(fetch_checks_for_package, packages.keys())
    for _ in tqdm(results, total=len(packages), unit="packages"):
        pass

    # Determine all checks being used and fill in packages with missing values.
    check_names = set()
    for package in packages.values():
        check_names |= set(package.checks.keys())
    check_names = sorted(check_names)
    fill_in_missing_checks(check_names)

    # Write all the data to a CSV for today
    write_packages_to_csv(check_names)

    # Write the data to the README
    write_packages_to_readme(check_names)


if __name__ == "__main__":
    main()
