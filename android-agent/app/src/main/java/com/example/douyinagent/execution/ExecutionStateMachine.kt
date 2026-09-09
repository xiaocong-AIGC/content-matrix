package com.example.douyinagent.execution

enum class ExecutionStep {
    CLAIMED,
    LAUNCHING_DOUYIN,
    OPENING_PUBLISH_ENTRY,
    SELECTING_PUBLISH_TYPE,
    SELECTING_MEDIA,
    EDITING_CONTENT,
    REVIEWING,
    WAITING_CONFIRMATION,
    VERIFYING_RESULT,
    COMPLETED,
    FAILED,
}

enum class DouyinPage {
    HOME,
    PUBLISH_ENTRY,
    TEXT_COMPOSER,
    TEXT_TEMPLATE,
    MEDIA_PICKER,
    EDITOR,
    PUBLISH_CONFIRM,
    PUBLISH_SUCCESS,
    SECURITY_CHALLENGE,
    UNKNOWN,
}

class ExecutionStateMachine {
    var step: ExecutionStep = ExecutionStep.CLAIMED
        private set

    fun moveTo(next: ExecutionStep) {
        if (step == ExecutionStep.COMPLETED || step == ExecutionStep.FAILED) return
        step = next
    }
}
