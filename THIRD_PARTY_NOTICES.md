# Third-party notices

The root [Apache-2.0 license](LICENSE) applies to Sturgis first-party work.
It does not replace third-party licenses or grant rights to provider brands,
model weights, external services, or research material.

| Component                                            | Notice and source record                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Vendored nlohmann/json C++ header                    | [Pinned source and hashes](native/memory/vendor/README.md), [MIT license](native/memory/vendor/LICENSE.MIT)                                                                                                                                                                                                              |
| 9router architecture and provider-protocol reference | [Gateway attribution and MIT notice](model_gateway/THIRD_PARTY_NOTICES.md)                                                                                                                                                                                                                                               |
| Client JavaScript packages and fonts                 | [Client notice assembly](frontend/licenses/README.md); the web build includes notices from the installed packages in `consumer/web/assets/`                                                                                                                                                                              |
| Documentation fonts                                  | [User-site font license](documentation/users/public/fonts/LICENSE.txt), [developer-site font license](documentation/developers/public/fonts/LICENSE.txt)                                                                                                                                                                 |
| Python, JavaScript, and documentation dependencies   | Exact direct requirements and lockfiles in [pyproject.toml](pyproject.toml), [package-lock.json](package-lock.json), [documentation/package-lock.json](documentation/package-lock.json), and [documentation/requirements.txt](documentation/requirements.txt); licenses remain in the respective installed distributions |

Retain applicable upstream notices when redistributing source, wheels, browser
assets, or documentation. The package build includes Sturgis's license and notices,
the native dependency notice, and the gateway's reference attribution. Dependency
or vendor updates must preserve the corresponding license text and source record.

Provider names describe integrations. They do not imply endorsement or transfer
rights to provider software, accounts, or models.
