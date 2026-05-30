package com.example.todoapp.ui

import android.content.Context
import android.view.LayoutInflater
import android.widget.NumberPicker
import com.example.todoapp.R
import com.example.todoapp.databinding.DialogAddTodoBinding
import com.google.android.material.dialog.MaterialAlertDialogBuilder

object AddTodoDialog {

    fun show(
        context: Context,
        onConfirm: (title: String, stars: Int, durationMinutes: Int) -> Unit
    ) {
        val binding = DialogAddTodoBinding.inflate(LayoutInflater.from(context))

        // Duration picker: 0–240 minutes in steps of 5
        binding.pickerDuration.apply {
            minValue = 0
            maxValue = 48           // 0..48  →  0..240 min
            value = 0
            displayedValues = (0..48).map { if (it == 0) "None" else "${it * 5}m" }.toTypedArray()
            wrapSelectorWheel = false
        }

        // Star picker
        var selectedStars = 0
        val starViews = listOf(
            binding.dialogStar1, binding.dialogStar2, binding.dialogStar3,
            binding.dialogStar4, binding.dialogStar5
        )
        fun refreshStars() {
            starViews.forEachIndexed { idx, view ->
                view.setImageResource(
                    if (idx < selectedStars) R.drawable.ic_star_filled
                    else R.drawable.ic_star_outline
                )
            }
        }
        starViews.forEachIndexed { idx, view ->
            view.setOnClickListener {
                selectedStars = if (selectedStars == idx + 1) 0 else idx + 1
                refreshStars()
            }
        }
        refreshStars()

        MaterialAlertDialogBuilder(context)
            .setTitle("New Name Board")
            .setView(binding.root)
            .setPositiveButton("Add") { _, _ ->
                val title = binding.etTitle.text?.toString()?.trim() ?: ""
                if (title.isNotEmpty()) {
                    val durationMinutes = binding.pickerDuration.value * 5
                    onConfirm(title, selectedStars, durationMinutes)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
