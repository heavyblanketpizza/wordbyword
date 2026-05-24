import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 1280
    height: 820
    minimumWidth: 1080
    minimumHeight: 700
    visible: true
    title: "wordbyword"
    color: "#f7f7f4"

    property int currentSection: 0
    property string uiFontFamily: "Arial"
    property string monoFontFamily: "Menlo"
    property var backend: (typeof bridge === "undefined" || bridge === null) ? null : bridge
    property var defaultJobState: ({
        "busy": false,
        "workflow": "",
        "phase": "idle",
        "detail": "Ready. Select a workflow and run the next action.",
        "status": "idle",
        "current": 0,
        "total": 0,
        "error": ""
    })
    property var safeSettings: backend ? backend.settings : ({})
    property var safeJobState: backend ? backend.jobState : defaultJobState
    property string safeLogText: backend ? backend.logText : ""
    property string safeArtifactText: backend ? backend.artifactText : "No artifacts yet."
    property bool busy: Boolean(safeJobState.busy)
    property bool runDrawerOpen: false
    property var definitions: backend ? backend.workflowDefinitions : []
    property var readinessMap: backend ? backend.workflowReadiness : ({})
    property var currentDefinition: definitions.length > currentSection ? definitions[currentSection] : ({})
    property var currentReadiness: readinessMap[currentDefinition.key] || ({})

    function commonValues() {
        return {
            "model": modelField.text,
            "base_url": baseUrlField.text
        }
    }

    function setupValues() {
        var values = commonValues()
        values.dictionary_input_dir = setupDictionaryInput.text
        values.class_source_dir = setupClassSource.text
        return values
    }

    function dictionaryValues() {
        var values = commonValues()
        values.dictionary_input_dir = dictionaryInput.text
        values.dictionary_output_dir = dictionaryOutput.text
        values.reference_dictionary_path = referenceDictionary.text
        values.dictionary_force = dictionaryForce.checked
        values.include_numbers = dictionaryNumbers.checked
        values.include_punctuation = dictionaryPunctuation.checked
        return values
    }

    function classPrintoutValues() {
        var values = commonValues()
        values.dictionary_output_dir = dictionaryOutput.text
        values.live_lexicon_path = liveLexicon.text
        values.class_source_dir = classSource.text
        values.source_output_root = parsedRoot.text
        values.rewrite_output_root = rewriteRoot.text
        values.print_output_root = printRoot.text
        values.rewrite_dictionary_path = rewriteDictionary.text
        values.force_class_printouts = forcePrintouts.checked
        values.skip_pdf_parse = skipPdfParse.checked
        values.pdf_render_dpi = pdfDpi.text
        values.print_font_path = printFont.text
        values.print_body_font_size = bodyFontSize.text
        return values
    }

    function archiveValues() {
        var values = commonValues()
        values.archive_root = archiveRoot.text
        values.usage_signal_output_root = usageOutputRoot.text
        values.canonical_dictionary_path = canonicalDictionary.text
        values.usage_candidates_path = usageCandidates.text
        values.usage_summary_path = usageSummary.text
        values.live_lexicon_path = liveLexicon.text
        values.supplemental_lexicon_path = supplementalLexicon.text
        values.unmatched_usage_output_path = unmatchedUsage.text
        values.include_near_duplicates = includeNearDuplicates.checked
        values.include_numbers = usageNumbers.checked
        values.include_punctuation = usagePunctuation.checked
        return values
    }

    function reportValues() {
        var values = commonValues()
        values.after_class_printout_path = reportPrintout.text
        values.after_class_report_output_root = reportOutputRoot.text
        values.after_class_report_font_path = reportFont.text
        return values
    }

    function workflowValues() {
        if (currentSection === 0)
            return setupValues()
        if (currentSection === 1)
            return dictionaryValues()
        if (currentSection === 2)
            return classPrintoutValues()
        if (currentSection === 3)
            return archiveValues()
        return reportValues()
    }

    function runWorkflow(workflowId) {
        var values = workflowValues()
        if (!workflowId || workflowId.length === 0) {
            saveSettings(values)
            return
        }
        runJob(workflowId, values)
    }

    function saveSettings(values) {
        if (!backend)
            return false
        return backend.saveSettings(values)
    }

    function runJob(workflowId, values) {
        if (!backend)
            return false
        return backend.runJob(workflowId, values)
    }

    function choosePath(current, directory) {
        if (!backend)
            return current
        return directory ? backend.chooseDirectory(current) : backend.chooseFile(current)
    }

    function backendPathForKey(key) {
        if (!backend)
            return ""
        return backend.pathForKey(key)
    }

    function statusLabel(status) {
        if (status === "missing_input")
            return "MISSING"
        if (status === "done")
            return "DONE"
        if (status === "running")
            return "RUNNING"
        if (status === "failed")
            return "FAILED"
        return "READY"
    }

    function statusTone(status) {
        if (status === "done")
            return "#111111"
        if (status === "running")
            return "#333333"
        if (status === "failed" || status === "missing_input")
            return "#ffffff"
        return "#f7f7f4"
    }

    function statusInk(status) {
        if (status === "done" || status === "running")
            return "#ffffff"
        return "#111111"
    }

    function pathForStageKey(key) {
        if (!key)
            return ""
        if (key === "dictionary_input_dir")
            return dictionaryInput.text
        if (key === "dictionary_output_dir")
            return dictionaryOutput.text
        if (key === "reference_dictionary_path")
            return referenceDictionary.text
        if (key === "dictionary_final")
            return dictionaryOutput.text + "/15_final_dictionary.jsonl"
        if (key === "class_source_dir")
            return classSource.text
        if (key === "source_output_root")
            return parsedRoot.text
        if (key === "rewrite_output_root")
            return rewriteRoot.text
        if (key === "print_output_root")
            return printRoot.text
        if (key === "vocabulary_source")
            return rewriteDictionary.text || liveLexicon.text || dictionaryOutput.text + "/15_final_dictionary.jsonl"
        if (key === "archive_root")
            return archiveRoot.text
        if (key === "usage_signal_output_root")
            return usageOutputRoot.text
        if (key === "live_lexicon_path")
            return liveLexicon.text
        if (key === "after_class_printout_path")
            return reportPrintout.text
        if (key === "after_class_report_output_root")
            return reportOutputRoot.text
        if (key === "model")
            return modelField.text
        return backendPathForKey(key)
    }

    component LabelText: Text {
        color: "#111111"
        font.family: root.uiFontFamily
        font.pixelSize: 12
        font.bold: true
    }

    component BodyText: Text {
        color: "#111111"
        font.family: root.uiFontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
        lineHeight: 1.15
    }

    component WTextField: TextField {
        id: control
        color: "#111111"
        selectedTextColor: "#ffffff"
        selectionColor: "#111111"
        font.family: root.uiFontFamily
        font.pixelSize: 13
        padding: 9
        background: Rectangle {
            color: "#ffffff"
            border.color: "#111111"
            border.width: 1
            radius: 0
        }
    }

    component WButton: Button {
        id: control
        property bool primary: false
        Layout.preferredHeight: 38
        enabled: !root.busy
        font.family: root.uiFontFamily
        font.pixelSize: 12
        font.bold: true
        background: Rectangle {
            color: control.down || control.primary ? "#111111" : (control.enabled ? "#ffffff" : "#e7e7e2")
            border.color: "#111111"
            border.width: control.primary ? 2 : 1
            radius: 0
        }
        contentItem: Text {
            text: control.text
            color: control.down || control.primary ? "#ffffff" : "#111111"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font: control.font
            elide: Text.ElideRight
        }
    }

    component WCheckBox: CheckBox {
        id: control
        spacing: 10
        font.family: root.uiFontFamily
        font.pixelSize: 13
        indicator: Rectangle {
            implicitWidth: 18
            implicitHeight: 18
            x: control.leftPadding
            y: parent.height / 2 - height / 2
            color: "#ffffff"
            border.color: "#111111"
            border.width: 1
            radius: 0
            Text {
                anchors.centerIn: parent
                text: control.checked ? "X" : ""
                color: "#111111"
                font.family: root.uiFontFamily
                font.pixelSize: 12
                font.bold: true
            }
        }
        contentItem: Text {
            text: control.text
            color: "#111111"
            font: control.font
            verticalAlignment: Text.AlignVCenter
            leftPadding: control.indicator.width + control.spacing
        }
    }

    component StatusPill: Rectangle {
        id: pill
        property string status: "ready"
        property string label: root.statusLabel(status)
        implicitWidth: pillText.implicitWidth + 18
        implicitHeight: 24
        color: root.statusTone(status)
        border.color: "#111111"
        border.width: 1
        radius: 0
        Text {
            id: pillText
            anchors.centerIn: parent
            text: pill.label
            color: root.statusInk(pill.status)
            font.family: root.uiFontFamily
            font.pixelSize: 10
            font.bold: true
        }
    }

    component SectionTitle: ColumnLayout {
        id: header
        property string title: ""
        property string purpose: ""
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Text {
                text: header.title
                color: "#111111"
                font.family: root.uiFontFamily
                font.pixelSize: 28
                font.bold: true
                Layout.fillWidth: true
            }
            StatusPill {
                status: String(root.currentReadiness.status || "ready")
                label: String(root.currentReadiness.label || root.statusLabel(root.currentReadiness.status || "ready"))
            }
        }
        BodyText {
            text: header.purpose
            color: "#333333"
            font.pixelSize: 14
            Layout.fillWidth: true
        }
    }

    component PathField: ColumnLayout {
        id: pathField
        property string label: ""
        property alias text: field.text
        property bool directory: true
        spacing: 6
        Layout.fillWidth: true
        Layout.maximumWidth: 960
        LabelText { text: pathField.label }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            WTextField {
                id: field
                Layout.fillWidth: true
                Layout.preferredHeight: 38
            }
            WButton {
                text: "..."
                Layout.preferredWidth: 44
                Layout.minimumWidth: 44
                Layout.maximumWidth: 44
                onClicked: {
                    field.text = root.choosePath(field.text, pathField.directory)
                    root.saveSettings(root.workflowValues())
                }
            }
        }
    }

    component FlowNavButton: Button {
        id: control
        property var definition: ({})
        property var readiness: ({})
        Layout.fillWidth: true
        Layout.preferredHeight: 70
        onClicked: root.currentSection = index
        background: Rectangle {
            color: root.currentSection === index ? "#111111" : "#ffffff"
            border.color: "#111111"
            border.width: root.currentSection === index ? 2 : 1
            radius: 0
        }
        contentItem: RowLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 10
            Text {
                text: String(control.definition.number || "")
                color: root.currentSection === index ? "#ffffff" : "#111111"
                font.family: root.uiFontFamily
                font.pixelSize: 12
                font.bold: true
                Layout.preferredWidth: 26
            }
            ColumnLayout {
                spacing: 4
                Layout.fillWidth: true
                Text {
                    text: String(control.definition.title || "")
                    color: root.currentSection === index ? "#ffffff" : "#111111"
                    font.family: root.uiFontFamily
                    font.pixelSize: 13
                    font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Text {
                    text: root.statusLabel(String(control.readiness.status || "ready"))
                    color: root.currentSection === index ? "#ffffff" : "#555555"
                    font.family: root.uiFontFamily
                    font.pixelSize: 10
                    font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }
    }

    component Panel: Rectangle {
        id: panel
        default property alias content: panelBody.data
        Layout.fillWidth: true
        color: "#ffffff"
        border.color: "#111111"
        border.width: 2
        radius: 0
        implicitHeight: panelBody.implicitHeight + 40
        ColumnLayout {
            id: panelBody
            anchors.fill: parent
            anchors.margins: 20
            spacing: 16
        }
    }

    component AdvancedBlock: ColumnLayout {
        id: block
        property string title: "Advanced"
        property bool open: false
        default property alias content: body.data
        Layout.fillWidth: true
        spacing: 12
        Button {
            id: advancedButton
            text: (block.open ? "- " : "+ ") + block.title
            Layout.preferredHeight: 34
            Layout.preferredWidth: 160
            font.family: root.uiFontFamily
            font.pixelSize: 12
            font.bold: true
            onClicked: block.open = !block.open
            background: Rectangle {
                color: "#ffffff"
                border.color: "#111111"
                border.width: 1
                radius: 0
            }
            contentItem: Text {
                text: advancedButton.text
                color: "#111111"
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                font: advancedButton.font
            }
        }
        ColumnLayout {
            id: body
            visible: block.open
            Layout.fillWidth: true
            spacing: 12
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 260
            Layout.fillHeight: true
            color: "#ffffff"
            border.color: "#111111"
            border.width: 2

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                Text {
                    text: "wordbyword"
                    color: "#111111"
                    font.family: root.uiFontFamily
                    font.pixelSize: 22
                    font.bold: true
                    Layout.bottomMargin: 8
                }

                BodyText {
                    text: "Beginner's Guide flow"
                    color: "#555555"
                    font.pixelSize: 12
                    Layout.bottomMargin: 4
                }

                Repeater {
                    model: root.definitions
                    delegate: FlowNavButton {
                        definition: modelData
                        readiness: root.readinessMap[modelData.key] || ({})
                    }
                }

                Item { Layout.fillHeight: true }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: runtimeBody.implicitHeight + 28
                    color: "#f7f7f4"
                    border.color: "#111111"
                    border.width: 1
                    ColumnLayout {
                        id: runtimeBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10
                        LabelText { text: "RUNTIME" }
                        WTextField {
                            id: modelField
                            Layout.fillWidth: true
                            text: String(root.safeSettings.model || "")
                            onEditingFinished: root.saveSettings(root.workflowValues())
                        }
                        WTextField {
                            id: baseUrlField
                            Layout.fillWidth: true
                            placeholderText: "Ollama base URL"
                            text: String(root.safeSettings.base_url || "")
                            onEditingFinished: root.saveSettings(root.workflowValues())
                        }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            ScrollView {
                id: mainScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                leftPadding: 24
                rightPadding: 24
                topPadding: 24
                bottomPadding: 24
                contentWidth: availableWidth

                ColumnLayout {
                    width: Math.min(mainScroll.availableWidth, 1080)
                    x: Math.max(0, (mainScroll.availableWidth - width) / 2)
                    spacing: 28

                    SectionTitle {
                        title: String(root.currentDefinition.number || "") + " / " + String(root.currentDefinition.title || "")
                        purpose: String(root.currentDefinition.purpose || "")
                        Layout.fillWidth: true
                    }

                    Panel {
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 14
                            LabelText { text: "END-TO-END FLOW" }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Repeater {
                                    model: root.definitions
                                    delegate: Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 74
                                        color: root.currentSection === index ? "#111111" : "#f7f7f4"
                                        border.color: "#111111"
                                        border.width: root.currentSection === index ? 2 : 1
                                        Text {
                                            anchors.fill: parent
                                            anchors.margins: 10
                                            text: String(modelData.number || "") + "\n" + String(modelData.title || "")
                                            color: root.currentSection === index ? "#ffffff" : "#111111"
                                            font.family: root.uiFontFamily
                                            font.pixelSize: 11
                                            font.bold: true
                                            wrapMode: Text.WordWrap
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Panel {
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 14
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                LabelText {
                                    text: "PIPELINE STAGES"
                                    Layout.fillWidth: true
                                }
                                BodyText {
                                    text: String(root.currentReadiness.next_action || "")
                                    color: "#555555"
                                    Layout.maximumWidth: 420
                                    horizontalAlignment: Text.AlignRight
                                }
                            }
                            Repeater {
                                model: root.currentDefinition.stages || []
                                delegate: Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: stageBody.implicitHeight + 22
                                    color: "#ffffff"
                                    border.color: "#111111"
                                    border.width: 1
                                    RowLayout {
                                        id: stageBody
                                        anchors.fill: parent
                                        anchors.margins: 11
                                        spacing: 14
                                        Rectangle {
                                            Layout.preferredWidth: 34
                                            Layout.preferredHeight: 34
                                            color: "#f7f7f4"
                                            border.color: "#111111"
                                            border.width: 1
                                            Text {
                                                anchors.centerIn: parent
                                                text: (index + 1 < 10 ? "0" : "") + String(index + 1)
                                                color: "#111111"
                                                font.family: root.uiFontFamily
                                                font.pixelSize: 11
                                                font.bold: true
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 5
                                            LabelText {
                                                text: String(modelData.label || "")
                                                font.pixelSize: 13
                                                Layout.fillWidth: true
                                            }
                                            BodyText {
                                                text: String(modelData.detail || "")
                                                color: "#333333"
                                                Layout.fillWidth: true
                                            }
                                            BodyText {
                                                visible: Boolean(modelData.output_path_key || modelData.input_path_key)
                                                text: {
                                                    var inputPath = root.pathForStageKey(String(modelData.input_path_key || ""))
                                                    var outputPath = root.pathForStageKey(String(modelData.output_path_key || ""))
                                                    var pieces = []
                                                    if (inputPath)
                                                        pieces.push(String(modelData.input_label || "Input") + ": " + inputPath)
                                                    if (outputPath)
                                                        pieces.push(String(modelData.output_label || "Output") + ": " + outputPath)
                                                    return pieces.join("    ")
                                                }
                                                color: "#666666"
                                                font.pixelSize: 11
                                                Layout.fillWidth: true
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Panel {
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 16
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    LabelText { text: "NEXT ACTION" }
                                    BodyText {
                                        text: String(root.currentReadiness.detail || "")
                                        color: "#333333"
                                        Layout.fillWidth: true
                                    }
                                    BodyText {
                                        text: String(root.currentReadiness.next_action || "")
                                        color: "#555555"
                                        Layout.fillWidth: true
                                    }
                                }
                                StatusPill {
                                    status: String(root.currentReadiness.status || "ready")
                                    label: String(root.currentReadiness.label || "")
                                }
                            }

                            StackLayout {
                                id: inputStack
                                currentIndex: root.currentSection
                                Layout.fillWidth: true

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: setupDictionaryInput; label: "Dictionary source folder"; text: String(root.safeSettings.dictionary_input_dir || "") }
                                    PathField { id: setupClassSource; label: "Class source folder"; text: String(root.safeSettings.class_source_dir || "") }
                                }

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: dictionaryInput; label: "Dictionary source folder"; text: String(root.safeSettings.dictionary_input_dir || "") }
                                    PathField { id: referenceDictionary; label: "Reference dictionary"; directory: false; text: String(root.safeSettings.reference_dictionary_path || "") }
                                    AdvancedBlock {
                                        title: "Advanced dictionary options"
                                        PathField { id: dictionaryOutput; label: "Dictionary output folder"; text: String(root.safeSettings.dictionary_output_dir || "") }
                                        RowLayout {
                                            spacing: 18
                                            WCheckBox { id: dictionaryForce; text: "Force rebuild"; checked: Boolean(root.safeSettings.dictionary_force) }
                                            WCheckBox { id: dictionaryNumbers; text: "Include numbers"; checked: Boolean(root.safeSettings.include_numbers) }
                                            WCheckBox { id: dictionaryPunctuation; text: "Include punctuation"; checked: Boolean(root.safeSettings.include_punctuation) }
                                        }
                                    }
                                }

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: classSource; label: "Class source folder"; text: String(root.safeSettings.class_source_dir || "") }
                                    AdvancedBlock {
                                        title: "Advanced printout options"
                                        PathField { id: parsedRoot; label: "Parsed output folder"; text: String(root.safeSettings.source_output_root || "") }
                                        PathField { id: rewriteRoot; label: "Rewrite output folder"; text: String(root.safeSettings.rewrite_output_root || "") }
                                        PathField { id: printRoot; label: "Print output folder"; text: String(root.safeSettings.print_output_root || "") }
                                        PathField { id: rewriteDictionary; label: "Override rewrite dictionary"; directory: false; text: String(root.safeSettings.rewrite_dictionary_path || "") }
                                        GridLayout {
                                            columns: 2
                                            columnSpacing: 18
                                            rowSpacing: 12
                                            WCheckBox { id: forcePrintouts; text: "Force printouts"; checked: Boolean(root.safeSettings.force_class_printouts) }
                                            WCheckBox { id: skipPdfParse; text: "Skip PDF parse"; checked: Boolean(root.safeSettings.skip_pdf_parse) }
                                            LabelText { text: "PDF render DPI" }
                                            WTextField { id: pdfDpi; text: String(root.safeSettings.pdf_render_dpi || "144"); Layout.preferredWidth: 140 }
                                            LabelText { text: "Body font size" }
                                            WTextField { id: bodyFontSize; text: String(root.safeSettings.print_body_font_size || ""); Layout.preferredWidth: 140 }
                                        }
                                        PathField { id: printFont; label: "Print font"; directory: false; text: String(root.safeSettings.print_font_path || "") }
                                    }
                                }

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: archiveRoot; label: "Class-session archive folder"; text: String(root.safeSettings.archive_root || "") }
                                    AdvancedBlock {
                                        title: "Archive maintenance and lexicon outputs"
                                        RowLayout {
                                            spacing: 12
                                            WButton { text: "Preview Rename"; Layout.preferredWidth: 150; onClicked: root.runJob("archive_rename_preview", root.archiveValues()) }
                                            WButton { text: "Apply Rename"; Layout.preferredWidth: 140; onClicked: root.runJob("archive_rename_apply", root.archiveValues()) }
                                        }
                                        WCheckBox { id: includeNearDuplicates; text: "Review near duplicates"; checked: Boolean(root.safeSettings.include_near_duplicates) }
                                        RowLayout {
                                            spacing: 12
                                            WButton { text: "Preview Duplicates"; Layout.preferredWidth: 170; onClicked: root.runJob("archive_duplicates_preview", root.archiveValues()) }
                                            WButton { text: "Apply Duplicates"; Layout.preferredWidth: 160; onClicked: root.runJob("archive_duplicates_apply", root.archiveValues()) }
                                        }
                                        PathField { id: usageOutputRoot; label: "Usage output folder"; text: String(root.safeSettings.usage_signal_output_root || "") }
                                        RowLayout {
                                            spacing: 18
                                            WCheckBox { id: usageNumbers; text: "Include numbers"; checked: Boolean(root.safeSettings.include_numbers) }
                                            WCheckBox { id: usagePunctuation; text: "Include punctuation"; checked: Boolean(root.safeSettings.include_punctuation) }
                                        }
                                        PathField { id: canonicalDictionary; label: "Canonical dictionary"; directory: false; text: String(root.safeSettings.canonical_dictionary_path || "") }
                                        PathField { id: usageCandidates; label: "Usage candidates"; directory: false; text: String(root.safeSettings.usage_candidates_path || "") }
                                        PathField { id: usageSummary; label: "Usage summary"; directory: false; text: String(root.safeSettings.usage_summary_path || "") }
                                        PathField { id: liveLexicon; label: "Live lexicon"; directory: false; text: String(root.safeSettings.live_lexicon_path || "") }
                                        PathField { id: supplementalLexicon; label: "Supplemental lexicon"; directory: false; text: String(root.safeSettings.supplemental_lexicon_path || "") }
                                        PathField { id: unmatchedUsage; label: "Unmatched usage"; directory: false; text: String(root.safeSettings.unmatched_usage_output_path || "") }
                                    }
                                }

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: reportPrintout; label: "Archived printout"; directory: false; text: String(root.safeSettings.after_class_printout_path || "") }
                                    PathField { id: reportOutputRoot; label: "Report output folder"; text: String(root.safeSettings.after_class_report_output_root || "") }
                                    AdvancedBlock {
                                        title: "Advanced report options"
                                        PathField { id: reportFont; label: "Report font"; directory: false; text: String(root.safeSettings.after_class_report_font_path || "") }
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                WButton {
                                    text: String(root.currentDefinition.primary_action_label || "Save")
                                    primary: true
                                    Layout.preferredWidth: 220
                                    onClicked: root.runWorkflow(String(root.currentDefinition.primary_workflow_id || ""))
                                }
                                WButton {
                                    visible: Boolean(root.currentDefinition.secondary_workflow_id)
                                    text: String(root.currentDefinition.secondary_action_label || "")
                                    Layout.preferredWidth: 190
                                    onClicked: root.runWorkflow(String(root.currentDefinition.secondary_workflow_id || ""))
                                }
                                WButton {
                                    text: "Refresh Status"
                                    Layout.preferredWidth: 140
                                    onClicked: root.saveSettings(root.workflowValues())
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: root.runDrawerOpen ? 236 : 72
                color: "#ffffff"
                border.color: "#111111"
                border.width: 2

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        StatusPill {
                            status: String(root.safeJobState.status || "idle")
                            label: String(root.safeJobState.status || "idle").toUpperCase()
                        }
                        BodyText {
                            text: String(root.safeJobState.detail || "Ready. Select a workflow and run the next action.")
                            color: "#333333"
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            maximumLineCount: 1
                        }
                        WButton {
                            text: root.runDrawerOpen ? "Hide Run Details" : "Show Run Details"
                            Layout.preferredWidth: 160
                            enabled: true
                            onClicked: root.runDrawerOpen = !root.runDrawerOpen
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 10
                        color: "#f7f7f4"
                        border.color: "#111111"
                        border.width: 1
                        Rectangle {
                            height: parent.height
                            width: {
                                var total = Number(root.safeJobState.total || 0)
                                var current = Number(root.safeJobState.current || 0)
                                if (total <= 0)
                                    return root.busy ? parent.width * 0.25 : 0
                                return Math.max(0, Math.min(parent.width, parent.width * current / total))
                            }
                            color: "#111111"
                        }
                    }

                    RowLayout {
                        visible: root.runDrawerOpen
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 12

                        TextArea {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            readOnly: true
                            text: root.safeLogText || "No run log yet."
                            wrapMode: TextArea.Wrap
                            color: "#111111"
                            font.family: root.monoFontFamily
                            font.pixelSize: 11
                            background: Rectangle {
                                color: "#ffffff"
                                border.color: "#111111"
                                border.width: 1
                                radius: 0
                            }
                        }

                        TextArea {
                            Layout.preferredWidth: 360
                            Layout.fillHeight: true
                            readOnly: true
                            text: root.safeArtifactText
                            wrapMode: TextArea.Wrap
                            color: "#111111"
                            font.family: root.monoFontFamily
                            font.pixelSize: 11
                            background: Rectangle {
                                color: "#ffffff"
                                border.color: "#111111"
                                border.width: 1
                                radius: 0
                            }
                        }
                    }
                }
            }
        }
    }
}
