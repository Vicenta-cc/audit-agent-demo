# 源码清单

P1原1570文件由固定Git提交提供；以下上游源码/依赖原样导入，不包含历史日志和报告。完整209文件含来源/目标/字节/摘要见INPUT_MANIFEST。新增测试实际运行版本见FINAL_TESTED_SOURCE；本轮新增输出单列OUTPUT_MANIFEST，不冒称上游已验收材料。

## 本轮新增实现与测试

| 文件 | 字节 | SHA256 |
|---|---:|---|
| experiments/geographic_joint_integration/adapter.py | 12003 | `e20ac7f5e821da3176a40b52b311ab13b400538d37cfe0146884c9a41cdec386` |
| experiments/geographic_joint_integration/audit.py | 3835 | `2cf1961ba4dfc6d9c2878dcd5809cfd8cc2bac811b15712ba8389ecab9113a22` |
| experiments/geographic_joint_integration/replay.py | 1413 | `161a5afd0bb452a00ddbb8632625f8294289cc5857e5ee1165e87ee3664d4c95` |
| experiments/geographic_joint_integration/run_offline.py | 9944 | `e5a27c42110fe85ef46cf15fa65be79fdf9a7e2c649b69f1e776d0a276552ebd` |
| experiments/geographic_joint_integration/scenarios.py | 10377 | `f5c298db84959b16aab5b2e1b4c65e7bbcedc85a4bc521cd94f6fe89632fcb92` |
| tests/test_geographic_joint_adapter.py | 10927 | `90c021a729ec837cf6f02e8f38c8806eae92a559065792ec8b9deb16c4e0cebf` |
| tests/test_geographic_joint_execution.py | 17998 | `f4888b2b7b62de23e69550afcd7ee7220d4d98b675109ccb7d4f6706980fdb34` |
| tests/test_geographic_joint_risk.py | 25724 | `ce35409cbc01e50b8b5032bb8c3984e822a15042255573aba8cfddda45f5fd1d` |

## 固定上游源码、测试与依赖

| 来源 | 文件 | 字节 | SHA256 |
|---|---|---:|---|
| p2 | backend/investigation_creation/scope_search_plan.py | 36720 | `6931b64f4ce432c3b07b5caf1a7560f5b54dd0bf31be16468564f54b9d294878` |
| p2 | experiments/geographic_execution_plan/authority.py | 15823 | `0717d854b12bf8aa39079d5ef3dac2ca17fb2a88be8e9b4533d515d350a67465` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/admin_catalog/SOURCE_MANIFEST.json | 3053 | `c6e87ce3ec8a57e2b50bf2914fd2b8883ba88cf9bc9c6bac3cca6d8ba1ee4af9` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/admin_catalog/geographic_catalog.json | 677345 | `716c35d358e030b8d499979884fce462d4a2d6293b76fb0bfaad8f59062f01a6` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/import_manifest.json | 3974 | `cb3a75f3255f4fc126880940ccfc717898536d2b236fc7a2f7b7020a487d6d18` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/location_catalog.json | 11323 | `5853d9666e1c8187f29f6c41831d59cb9d78e7a03ae69941f6e4afdf75b16f5d` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/relevance.py | 63341 | `6be6a45edf64f4896be3dbe28ab8b9115e3eea1148225076c10c3d617a52df1e` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/relevance_ip.py | 9940 | `e8aceda603ac94d45146c49756d5b81a09f110f5ff28023b44ee2a426a1f1efb` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/relevance_post_only.py | 14781 | `84e9c5a992f87e73fe4cd271b0b634cd75bc87e2e7dad089522208b430abcfe5` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/relevance_post_only_names.py | 6354 | `a5a7a5c66d32e78e872fc879ab4e199be9715d4df25a58f06e1f3e90b46d327e` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/relevance_post_only_names_v2.py | 8739 | `1ecc04aa18af97ed9fef0c00a5ebbd95b4f9ed8b5e23b27dbcd7b4d373fe4907` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/relevance_post_only_simple.py | 7558 | `899d052bf85616f44f2ff82a4e61986f5340be4fc962290b00d688bff5e515a9` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/relevance_post_only_v2.py | 14006 | `b38510d1eda2f0957b0d87a6e6f8be394af136390855748bf662c8f831f89607` |
| p2 | experiments/geographic_execution_plan/dependencies/p3/relevance_scope.py | 8625 | `6980b5ecdb00bbbbba68c0dcc1aafc007b2f88fd3b8674e405ff1e9703c82eff` |
| p2 | experiments/geographic_execution_plan/frozen_risk.py | 7278 | `dec86f5f877ccbd16d7fac2201e2168f4090d0619dfa608e2d3fbd0631df2244` |
| p2 | experiments/geographic_execution_plan/offline_guards.py | 9685 | `0b09f7eb951a0647da64d8bbd8e93db4414246365a4d2d1f0c5ccdc9242d3492` |
| p2 | experiments/geographic_execution_plan/p3_bridge.py | 7754 | `5ed8fbebdeb17e652d502ae862e36a22fe5549555c7539f1718a82ced93a21c5` |
| p2 | experiments/geographic_execution_plan/replay.py | 2495 | `461799c3e6fc7e3ba4ccc59168be70ae7cfe81593c9ff2a5344807d1609c3af4` |
| p2 | experiments/geographic_execution_plan/run_offline.py | 10814 | `8bc690c606ab0dac837c9023deae6f172862801a54ea7a873bbe397496490283` |
| p2 | experiments/geographic_execution_plan/storage.py | 4588 | `d25e8722775a8f3c33c215d8df924ddfd3b50b959230fdd22097b06910f44d0e` |
| p2 | experiments/geographic_execution_plan/verify_sources.py | 4885 | `6897cc1b416baff015974ae1b92b025a7aea7b884c1e7c395a3411389421ee21` |
| p2 | tests/test_geographic_execution_plan.py | 7580 | `201f31f0c7bc31bd9487887225e57ddac3b9ff0d7c5d6230b3adce88b1ce8a28` |
| p2 | tests/test_geographic_execution_plan_guards.py | 5651 | `57573e63d175c08f35139a712c7494c4b9bce2f18e69f0389afd35371a7f80a5` |
| p2 | tests/test_geographic_execution_plan_integration.py | 10422 | `34a7aa33f830c4d69dcb1ee3329c3079da48ad800dda0455c8601a02094b59cb` |
| p2 | tests/test_geographic_execution_plan_p3.py | 12091 | `ddf0c8aeaf4b38ea51e72bd400d093680ab56c418df7a42f1cb31d7e35d019f1` |
| p2 | tests/test_geographic_execution_plan_provenance.py | 4708 | `4ea4a56b289c1d9e10bca2b3c4e4e217dea2dc7b377f1c7c0ea387b52c2d1ae7` |
| p2 | tests/test_geographic_execution_plan_resources.py | 9957 | `3b1aeaf69d9432fd0f4485a1d05ff09fba401b8d8db1d8a725b40a37383a35dc` |
| p2 | tests/test_geographic_execution_plan_risk.py | 19184 | `2da5af3bbe9cebeedffe5e66f76859ba4bdb68da67fb1bec1dad0196e6c3610d` |
| p2 | tests/test_geographic_execution_plan_storage.py | 7534 | `bd3668f3dd39f1d94d421e9405f706f6627ef86f084cebf8beff065d5fad77f4` |
| p4 | backend/audit_agent/scoped_execution.py | 17549 | `4d4d998c6b2f24833280d41bdda22df4d48b04a208662df03f68b7640cd1b5e0` |
| p4 | backend/audit_agent/scoped_selection.py | 19792 | `9449f1828bcf493f2b604a11b99fe8cb3f67647605cf8f720b44a67cb02f36ac` |
| p4 | backend/audit_agent/scoped_selection_store.py | 25499 | `317b9c77309a4189db37b32ca1fcaffef1967e7d03455f18ae26a9ccc9cadf7f` |
| p4 | experiments/geographic_selection/.gitignore | 64 | `4a8b51ab9f1a25f1218a593cd5ef9fece520352b5d75ab0f011476d74a2100f7` |
| p4 | experiments/geographic_selection/bridge.py | 8513 | `a3cbea769e9956de0fa8e6056bbf27fe6fba746c2bfe1f7091bdd336da3e392c` |
| p4 | experiments/geographic_selection/demo.py | 4410 | `cece3c14e66324c0bcf2d052bf7c787442affc8a082fb4f34e16b134764eb38f` |
| p4 | experiments/geographic_selection/dependencies/IMPORT_MANIFEST.json | 3153 | `c7d17f75b673babc6d1a8cb2d8bd95a31a68e34bece311dd1765380f47889a57` |
| p4 | experiments/geographic_selection/dependencies/__init__.py | 62 | `95e5f45ce6e2204058e6067779882c3051cc8ced001963b1704926aa02d92bdc` |
| p4 | experiments/geographic_selection/dependencies/p3/__init__.py | 62 | `95e5f45ce6e2204058e6067779882c3051cc8ced001963b1704926aa02d92bdc` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/__init__.py | 62 | `95e5f45ce6e2204058e6067779882c3051cc8ced001963b1704926aa02d92bdc` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/__init__.py | 62 | `95e5f45ce6e2204058e6067779882c3051cc8ced001963b1704926aa02d92bdc` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/relevance.py | 63341 | `6be6a45edf64f4896be3dbe28ab8b9115e3eea1148225076c10c3d617a52df1e` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/relevance_ip.py | 9940 | `e8aceda603ac94d45146c49756d5b81a09f110f5ff28023b44ee2a426a1f1efb` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/relevance_post_only.py | 14781 | `84e9c5a992f87e73fe4cd271b0b634cd75bc87e2e7dad089522208b430abcfe5` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/relevance_post_only_names.py | 6354 | `a5a7a5c66d32e78e872fc879ab4e199be9715d4df25a58f06e1f3e90b46d327e` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/relevance_post_only_names_v2.py | 8739 | `1ecc04aa18af97ed9fef0c00a5ebbd95b4f9ed8b5e23b27dbcd7b4d373fe4907` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/relevance_post_only_simple.py | 7558 | `899d052bf85616f44f2ff82a4e61986f5340be4fc962290b00d688bff5e515a9` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/relevance_post_only_v2.py | 14006 | `b38510d1eda2f0957b0d87a6e6f8be394af136390855748bf662c8f831f89607` |
| p4 | experiments/geographic_selection/dependencies/p3/backend/audit_agent/relevance_scope.py | 8625 | `6980b5ecdb00bbbbba68c0dcc1aafc007b2f88fd3b8674e405ff1e9703c82eff` |
| p4 | experiments/geographic_selection/dependencies/p3/experiments/geographic_relevance/admin_catalog/SOURCE_MANIFEST.json | 3053 | `c6e87ce3ec8a57e2b50bf2914fd2b8883ba88cf9bc9c6bac3cca6d8ba1ee4af9` |
| p4 | experiments/geographic_selection/dependencies/p3/experiments/geographic_relevance/admin_catalog/geographic_catalog.json | 677345 | `716c35d358e030b8d499979884fce462d4a2d6293b76fb0bfaad8f59062f01a6` |
| p4 | experiments/geographic_selection/dependencies/p3/experiments/geographic_relevance/location_catalog.json | 11323 | `5853d9666e1c8187f29f6c41831d59cb9d78e7a03ae69941f6e4afdf75b16f5d` |
| p4 | experiments/geographic_selection/fixtures.py | 3527 | `5d70b3c496135ea35aa7fcfd3907f13ffba74195011afc9add674073648a5221` |
| p4 | experiments/geographic_selection/offline_guard.py | 2898 | `6e601d4758e2111c64248bb7185ea981eeb7b9bb43216baad0b4cc410c61d4e1` |
| p4 | experiments/geographic_selection/pinned_docs/geographic-relevance-p2-pinned-inputs-v2.json | 8366 | `29152dbfb0c27a96d046d491c45d846e3524395a6fb7412dd4ee1afde09bdd32` |
| p4 | experiments/geographic_selection/pinned_docs/geographic-relevance-p4-pinned-inputs-v1.json | 10562 | `93fda5e57b2bf492bb759976443c252398aeb3684b40d1fa95dcad1959f42d72` |
| p4 | experiments/geographic_selection/preflight.json | 12395 | `227e08ebcd24e2bae8247971e010525257b8e3aafacee39eb3a0b38d73104ee9` |
| p4 | experiments/geographic_selection/run_checks.py | 3250 | `32f0692d775556660bc4bec134c6417bb594dddadd8805a6cb8a48b49d8920c6` |
| p4 | experiments/geographic_selection/verify_inputs.py | 2235 | `e0f773c625e39bd09bd5ba550e8b8a9e3b4c2f5ce04b843f6e3165bb6d6f14cd` |
| p4 | tests/test_scoped_bridge.py | 8005 | `a52b5ed5309421b120572c52a8e980d3174180b6ed458a4de702fe0d8b8d9ac8` |
| p4 | tests/test_scoped_execution.py | 16999 | `519d9da54991c89a25ebd0b21e83f4959553a1bc45ce5d9918efc087d63575bc` |
| p4 | tests/test_scoped_selection.py | 13384 | `45ef427a90766220e43bb38cb497543ac9def462eb69dd0cbfb0180dd9680a99` |
| p4 | tests/test_scoped_selection_store.py | 17404 | `9a96895e9f48fbdcb404ac823813b1d6e9c384e7fb3f4f8593ebd8d3f1f769e9` |
