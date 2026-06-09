package com.example.todoapp.ui

import android.content.Context
import com.example.todoapp.data.TodoItem
import com.google.android.material.dialog.MaterialAlertDialogBuilder

object DeleteConfirmDialog {
    fun show(context: Context, item: TodoItem, onConfirm: () -> Unit) {
        MaterialAlertDialogBuilder(context)
            .setTitle("Delete Board")
            .setMessage("Remove \"${item.title}\"?")
            .setPositiveButton("Delete") { _, _ -> onConfirm() }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
