{% load debate_tags %}
{% load staticfiles %}

$(document).ready( function() {

// UTILITY FUNCTIONS

  function DOMIdtoInt(e) {
    return parseInt($(e).attr('id').split('_')[1]);
  }

  function formatScore(n) {
    return n.toPrecision(2);
  }

// UI INITIALISATION FUNCTIONS

  function display_conflicts(target) {
    eachConflictingTeam(DOMIdtoInt(target),
      function (type, elem) {
        $(elem).addClass(conflictTypeClass[type]);
      }
    );
  }

  function remove_conflicts(target) {
    eachConflictingTeam(DOMIdtoInt(target),
      function (type, elem) {
        $(elem).removeClass(conflictTypeClass[type]);
      }
    );
  }

  function init_adj(el) {

    el.mouseover( function(e) {
      if (draggingCurrently === false) {
        display_conflicts(e.currentTarget);
      }
    });

    el.mouseout( function(e) {
      remove_conflicts(e.currentTarget);
      update_all_conflicts(); // Need to check we haven't removed in-situ conflicts
    });

    el.draggable({
      containment: "body", // bounds that limit dragging area
      helper: function() {
        this.oldHolder = $(this).parent("td");
        var adj = $(this).clone();
        // $(this).css('position', 'relative');
        // var offset = $(this).offset();
        // $(adj).appendTo('body').css('top', 0).css('left', 0);
        // var curOff = $(adj).offset();
        // $(adj).css('top', offset.top - curOff.top).css('left', offset.left - curOff.left);
        return adj;
      },
      revert: 'invalid',
      start: function(event, ui) {
        // $("#" + ui.helper.attr("id")).addClass("dragging");
        // $(ui.helper).addClass("dragging");
        // We want to keep showing conflicts during drag, so
        // we unbind the event
        display_conflicts(event.currentTarget);
        $(event.currentTarget).unbind('mouseover mouseout');
        draggingCurrently = true;
      },
      stop: function(event, ui) {
        target_id = $("#" + ui.helper.attr("id"));
        // If the mouse isn't over the original position, stop highlighting.
        // Set a timeout to run shortly after to give the element time to get
        // back where it belongs. (If there's a way to do this without setTimeout,
        // that is probably preferable.)
        // setTimeout( function() {
        //   if ($("#" + ui.helper.attr("id") + ":hover").length == 0) {
        //     eachConflictingTeam(
        //       DOMIdtoInt(target_id),
        //       function (type, elem) {
        //         $(elem).removeClass(conflictTypeClass[type]);
        //       }
        //     );
        //   }
        // }, 50)
        $(ui.helper).remove();

        // When dropping need to remove the drag class and rebind unbound events
        // $("#" + ui.helper.attr("id")).bind( "mouseover", function(e) {
        //   display_conflicts(e.currentTarget);
        // }).bind( "mouseout", function(e) {
        //   remove_conflicts(e.currentTarget);
        // }).removeClass("dragging");
        draggingCurrently = false;

        console.log(ui.helper);
        update_all_conflicts(); // Update to account for new/resolved conflicts
      }
    });
  }

  // Used by auto-allocation; removes all data before the auto sweep
  function reset() {
    $('.adj').remove();
    unusedAdjTable.clear();
  }

// DATA INITIALISATION FUNCTIONS

  function load_adjudicator_scores(callback) {
    $.getJSON("{% tournament_url adj_scores %}", function(data) {
      all_adj_scores = data;
      if (callback) callback();
    });
  }

  function load_allocation_data(data) {
    $.each(data.debates, function(debate_id, adj_data) {
      if (adj_data.chair) {
        set_chair(debate_id, adj_data.chair);
        {% if duplicate_adjs %} // If duplicating adjs need to copy over those allocated
        moveToUnused(_make_adj(adj_data.chair));
        {% endif %}
      }
      clear_panel(debate_id);
      $.each(adj_data.panel, function(idx, adj) {
        add_panellist(debate_id, adj);
        {% if duplicate_adjs %} // If duplicating adjs need to copy over those allocated
        moveToUnused(_make_adj(adj));
        {% endif %}
      });
      clear_trainees(debate_id);
      $.each(adj_data.trainees, function(idx, adj) {
        add_trainee(debate_id, adj);
        {% if duplicate_adjs %} // If duplicating adjs need to copy over those allocated
        moveToUnused(_make_adj(adj));
        {% endif %}
      });

    });
    $.each(data.unused, function(idx, adj_data) {
      moveToUnused(_make_adj(adj_data));
    });
  }

  function load_allocation(callback) {
    $.getJSON("{% round_url draw_adjudicators_get %}", function(data){
      load_allocation_data(data);
      callback();
    });
  }

  function load_conflict_data() {
    $.getJSON("{% round_url adj_conflicts %}", function(data) {
      all_adj_conflicts = data;
      $(".adj").each( function() {
        // insert blank entries for adjs who aren't there (those without conflicts)
        var id = DOMIdtoInt(this);
        if (all_adj_conflicts['personal'][id] == undefined) { all_adj_conflicts['personal'][id] = []; }
        if (all_adj_conflicts['history'][id] == undefined) { all_adj_conflicts['history'][id] = []; }
        if (all_adj_conflicts['institutional'][id] == undefined) { all_adj_conflicts['institutional'][id] = []; }
        if (all_adj_conflicts['adjudicator'][id] == undefined) { all_adj_conflicts['adjudicator'][id] = []; }
      });
      update_all_conflicts();
    });
  }

  function append_adj_scores() {
    $(".adj").each(function() {
      $(this).prop('title', $(this).children("span").text());
      $(this).children("span").prepend('<a data-toggle="modal" data-target="#adj-feedback" title="Click to view feedback history" data-toggle="tooltip" class="info">' + formatScore(all_adj_scores[DOMIdtoInt(this)]) + '</a> ');
      $("a", this).click(function() {
        // Function to handle opening the modal window
        var adj_row = $(this).parent().parent(); // Going back up to the div with the id
        var adj_name = adj_row.attr('title');
        var adj_id = DOMIdtoInt(adj_row);
        $("#modal-adj-name").text(adj_name); // Updating header of the modal
        var adj_feedback_url = '{% tournament_url get_adj_feedback %}?id=' + adj_id;
        adjFeedbackModalTable.ajax.url(adj_feedback_url).load();
      }).tooltip();
    });
  }

// UI BEHAVIOURS

  // Toggling the unused column from horizontal to vertical arrangements
  $('#toggle_unused_layout').click(function() {
    if ($('#scratch').hasClass("fixed-right")) {
      $('#scratch').removeClass("fixed-right").addClass("fixed-bottom");
      $('#main').removeClass("col-xs-10").addClass("col-xs-2");
    } else {
      $('#scratch').removeClass("fixed-bottom").addClass("fixed-right");
      $('#main').addClass("col-xs-10").removeClass("col-xs-2");
    }
    return false
  });

  $('#toggle_gender').click(function() {
    var columnA = allocationsTable.column(4);
    columnA.visible( ! columnA.visible() );
    var columnB = allocationsTable.column(7);
    columnB.visible( ! columnB.visible() );
    $(".adj").toggleClass("gender-display");
    $(".gender-highlight").toggleClass("gender-display");
    $("span", this).toggleClass("glyphicon-eye-open").toggleClass("glyphicon-eye-close");
    return false
  });

  $('#toggle_region').click(function() {
    $("span", this).toggleClass("glyphicon-eye-open").toggleClass("glyphicon-eye-close");
    return false
  });

  $('#toggle_language').click(function() {
    $("span", this).toggleClass("glyphicon-eye-open").toggleClass("glyphicon-eye-close");
    return false
  });

  $('#toggle_venues').click(function() {
    var venuesColumn = allocationsTable.column(2);
    venuesColumn.visible( ! venuesColumn.visible() );
    $("span", this).toggleClass("glyphicon-eye-open").toggleClass("glyphicon-eye-close");
    return false
  });

  $('#toggle_wins').click(function() {
    var affWinsColumn = allocationsTable.column(3);
    affWinsColumn.visible( ! affWinsColumn.visible() );
    var negWinsColumn = allocationsTable.column(6);
    negWinsColumn.visible( ! negWinsColumn.visible() );
    $("span", this).toggleClass("glyphicon-eye-open").toggleClass("glyphicon-eye-close");
    return false
  });

// CONFLICT BEHAVIOURS

  // Read the dicitionary and check if the adj has any conflicts
  function eachConflictingTeam(adj_id, fn) {
    $.each(all_adj_conflicts['personal'][adj_id], function (i, n) {
      $("#team_" + n).each( function() { fn('personal', this); });
    });
    $.each(all_adj_conflicts['history'][adj_id], function (i, n) {
      $("#team_" + n).each( function() { fn('history', this); });
    });
    $.each(all_adj_conflicts['institutional'][adj_id], function (i, n) {
      $("#team_" + n).each( function() { fn('institutional', this); });
    });
    $.each(all_adj_conflicts['adjudicator'][adj_id], function (i, n) {
      $("#adj_" + n).each( function() { fn('adjudicator', this); });
    });
  }

  // Checks/highlights any existing conflicts on in-place data
  function update_all_conflicts() {
    $(".teaminfo", ".adj").removeClass("personal-conflict institutional-conflict history-conflict")
    $("#allocationsTable tbody tr").each( function() {
      updateConflicts(this);
    });
  }

  // Checks an individual debate for circumstances of conflict
  function updateConflicts(debate_tr) {
    // var ca = 0;
    // var ch = 0;
    // var ci = 0;
    $(".adj", debate_tr).each( function() {
      var adj = this;
      var adj_id = DOMIdtoInt(this);
      // var a = 0;
      // var h = 0;
      // var i = 0;
      // select the Aff & Neg team
      $("td.teaminfo", debate_tr).each( function() {

        if ($.inArray(DOMIdtoInt(this),all_adj_conflicts['personal'][adj_id]) != -1) {
          $(this).addClass("personal-conflict");
          $(adj).addClass("personal-conflict");
          //ca++;
          //a++;
        } else if ($.inArray(DOMIdtoInt(this),all_adj_conflicts['institutional'][adj_id]) != -1) {
          $(this).addClass("institutional-conflict");
          $(adj).addClass("institutional-conflict");
          //ci++;
          //i++;
        } else if ($.inArray(DOMIdtoInt(this), all_adj_conflicts['history'][adj_id]) != -1) {
          $(this).addClass("history-conflict");
          $(adj).addClass("history-conflict");
          //ch++;
          //h++;
        }
      });
      // if (a == 0) {
      //   $(adj).removeClass("personal-conflict");
      // }
      // if (h == 0) {
      //   $(adj).removeClass("history-conflict");
      // }
      // if (i == 0) {
      //   $(adj).removeClass("institutional-conflict");
      // }
    });

    // if (ca == 0) {
    //   $(debate_tr).removeClass("debate-inactive");
    // }

    // Check for incomplete panels
    if ($(".panel-holder .adj", debate_tr).length % 2 != 0) {
      $(".panel-holder", debate_tr).addClass("incomplete");
    } else {
      $(".panel-holder", debate_tr).removeClass("incomplete");
    }

    // Check for missing chairs
    if ($(".chair-holder .adj", debate_tr).length != 1) {
      $(".chair-holder", debate_tr).addClass("incomplete");
    } else {
      $(".chair-holder", debate_tr).removeClass("incomplete");
    }

  }

// TABLE BEHAVIOURS

  // Enabling the priority to be editable; and submitting AJAX updates of its values
  $('#allocationsTable .importance').editable('{% round_url update_debate_importance %}', {
    "callback": function(sValue, y) {
      allocationsTable.cell(this).data(sValue); // Update the datatable's value
    },
    submitdata: function(value, settings) {
      return {"debate_id": this.parentNode.getAttribute('id').replace('debate_','')};
    },
    type: 'select',
    onblur: 'submit',
    data: "{'1':'1', '2':'2', '3':'3', '4':'4', '5':'5'}"
  });

  $("#scratch").droppable( {
    accept: '.adj',
    hoverClass: 'bg-success',
    drop: function(event, ui) {
      var adj = ui.draggable;
      remove_conflicts(adj);
      moveToUnused(adj);

      //var oldHolder = adj[0].oldHolder;
      // adj.animate({'top': '0', 'left': '0'}, 300);
      //adj.removeClass("personal-conflict").removeClass("history-conflict").removeClass("institutional-conflict"); // Remove conflict classes when dropped
      //updateConflicts($(oldHolder).parent("tr"));
    }
  });


  $("#allocationsTable .adj-holder").droppable( {
    hoverClass: 'bg-info',
    drop: function(event, ui) {
      var adj = ui.draggable;
      var oldHolder = adj[0].oldHolder; // Where the element came from
      var destinationAdjs = $(".adj", this);

      // var newHomeOff;
      // var curOff = adj.offset();

      if (destinationAdjs.length == 0 || $(this).hasClass("panel-holder") || $(this).hasClass("trainee-holder")) {
        // If replacing an empty chair, or adding to a panel/trainee list
        // replacing = $(document.createElement("div"));
        // replacing.addClass("adj");
        // replacing[0].style.visibility = "hidden";
        // $(this).append(replacing);
        // newHomeOff = replacing.offset();
        // replacing.remove();
      } else {
        // If replacing an existing chair
        //newHomeOff = replacing.offset();
        if (oldHolder.hasClass("adj-holder")) {
          oldHolder.append(destinationAdjs); // Swap the two around
        } else {
          moveToUnused(destinationAdjs);
        }
      }

      $(this).append(adj);

      // If coming from the unused table, delete that row
      // if ($(oldHolder).hasClass("unused")) {
      //   {% if not duplicate_adjs %}
      //     var idx = unusedTable.fnGetPosition(oldHolder[0]);
      //     unusedTable.fnDeleteRow(idx[0]);
      //   {% endif %}
      // }

      // {% if duplicate_adjs %}
      // if ($(oldHolder).hasClass("unused")) {
      //   // If duplicate we clone a (deep) copy to leave behind
      //   var clone = $(adj).clone().appendTo(oldHolder);
      //   clone.removeAttr('style');
      //   init_adj(clone);
      // }
      // {% endif %}


      // adj.css('top', curOff.top - newHomeOff.top).css('left', curOff.left - newHomeOff.left);
      // adj.animate({'top': '0', 'left': '0'}, 300);
      //updateConflicts(oldHolder.parent("tr"));
      //updateConflicts($(this).parent("tr"));



    }

  });



  $('#auto_allocate').click(function() {
    var btn = $(this)
    btn.button('loading')

    $.ajax({
      type: "POST",
      url: "{% round_url create_adj_allocation %}",
      success: function(data, status) {
        reset();
        load_allocation_data($.parseJSON(data));
        update_all_conflicts();
        append_adj_scores();
        $('#loading').hide();
        btn.button('reset')
      },
      error: function(xhr, error, ex) {
        $("#alerts-holder").html('<div class="alert alert-danger alert-dismissable" id=""><button type="button" class="close" data-dismiss="alert">&times;</button>Auto-allocation failed! '
          + xhr.responseText + ' (' + xhr.status + ')</div>');
        $(this).button('reset');
        btn.button('reset')
      }
    });
  });

  $('#save').click( function() {
    var btn = $(this)
    btn.button('loading')
    var data = {};

    $("#allocationsTable tbody tr").each( function() {
      var debateId = DOMIdtoInt(this); // Purpose of the value is to ID this debate as being saved, so if following values are blank it is still processed
      data['debate_' + debateId] = true;
      $(".chair-holder .adj", this).each( function() {
        data['chair_' + debateId] = DOMIdtoInt(this);
      });
      data['panel_' + debateId]  = [];
      $(".panel-holder .adj", this).each( function() {
        data['panel_' + debateId].push(DOMIdtoInt(this));
      });
      data['trainees_' + debateId]  = [];
      $(".trainee-holder .adj", this).each( function() {
        data['trainees_' + debateId].push(DOMIdtoInt(this));
      });
    });

    $.ajax( {
      type: "POST",
      url: "{% round_url save_adjudicators %}",
      data: data,
      success: function(data, status) {
        btn.button('reset')
        $("#alerts-holder").html('<div class="alert alert-success alert-dismissable" id=""><button type="button" class="close" data-dismiss="alert">&times;</button>Saved successfully!</div>');
      },
      error: function(xhr, error, ex) {
        btn.button('reset')
        $("#alerts-holder").html('<div class="alert alert-danger alert-dismissable" id=""><button type="button" class="close" data-dismiss="alert">&times;</button>Saved failed!</div>');
      }
    });

    return false;
  });

// ALLOCATION MANIPULATION FUNCTIONS

  function _make_adj(data) {
    var adj = $('<div></div>').addClass('adj btn btn-block').attr('id', 'adj_' + data.id).append($('<span></span> ').html(data.name))
    init_adj(adj);
    return adj;
  }

  function set_chair(debate_id, data) {
    var td = $('#chair_'+debate_id);
    $('div.adj', td).remove();
    _make_adj(data).appendTo(td);
  }

  function clear_panel(debate_id) {
    $('#panel_'+debate_id).find('div.adj').remove();
  }

  function clear_trainees(debate_id) {
    $('#trainees_'+debate_id).find('div.adj').remove();
  }

  function add_panellist(debate_id, data) {
    var td = $('#panel_'+debate_id);
    _make_adj(data).appendTo(td);
  }

  function add_trainee(debate_id, data) {
    var td = $('#trainees_'+debate_id);
    _make_adj(data).appendTo(td);
  }

  function moveToUnused(adj) {
    // Build a list of all adjs already on the tab;e
    var unusedIDs = [];
    $("#unusedAdjTable .adj").each(function(){
      unusedIDs.push(DOMIdtoInt(this));
    });
    var moving_adj_id = DOMIdtoInt(adj);

    if (unusedIDs.indexOf(moving_adj_id) == -1) {
      // If the adj isn't already in the table
      var new_row = unusedAdjTable.row.add( ["",formatScore(all_adj_scores[moving_adj_id])] ).draw(); // Adds a new row
      var first_cell = $("td:first", new_row.node()).append(adj); // Append the adj element

      //unusedAdjTable.cell(0, 0).append(adj);
      //unusedAdjTable.draw(); // Update the table

      // var idxs = unusedTable.a();
      // var trNode = unusedTable.fnGetNodes(idxs[0]);
      // var td = $("td:first", trNode);
      // td.addClass("unused");
      // // append node (to preserve events)
      // td.children().remove();
      // td;
    } else {
      // Adj is already in the table (might just be dragging back)
      var oldHolder = adj[0].oldHolder;
      if ($(oldHolder).hasClass("unused") === false) {
        $(adj).remove();
      }
    }
  }

// INITIALISATION VARIABLES

  // Dictionary matching scores to adj_id, ie 279:5
  var all_adj_scores;
  // A list of bjects for each conflict type. Each has a list of Adj IDs with an array of conflict IDs
  var all_adj_conflicts;
  var conflictTypeClass = {
    'personal': 'personal-conflict',
    'institutional': 'institutional-conflict',
    'history': 'history-conflict',
    'adjudicator': 'adjudicator-conflict',
  }
  // Global dragging variable; to stop highlights on other teams being shown while dragging
  var draggingCurrently = false;


// DATATABLE INITIALISATION

  // Setup main table
  var allocationsTable = $("#allocationsTable").DataTable( {
    "bAutoWidth": false,
    "aoColumns": [
      { "sWidth": "3%" },
      { "sWidth": "3%" },
      { "sWidth": "3%" },
      { "sWidth": "3%" },
      { "sWidth": "3%" },
      { "sWidth": "17%" },
      { "sWidth": "3%" },
      { "sWidth": "3%" },
      { "sWidth": "17%" },
      { "sWidth": "18%" },
      { "sWidth": "18%" },
      { "sWidth": "18%" }
    ],
    "aaSorting": [[1, 'desc']],
    "aoColumnDefs": [
      { "bVisible": false, "aTargets": [2,3,4,6,7] }, //set column visibility
    ]
  });

  // Setup unused table
  var unusedAdjTable = $("#unusedAdjTable").DataTable({
    aoColumns: [
      { "sWdith": "90%", "sType": "string" },
      { "sWidth": "10%", "sType": "string" }
    ],
    "aaSorting": [[1, 'desc'], [0, 'desc']],
    "aoColumnDefs": [
      // Sort based on feedback despite it being a hidden column
      {"iDataSort": 1, "aTargets": [0] },
      { "bVisible": false, "aTargets": [1] },
    ],
    "autoWidth": false,
    bFilter: false,
  })

  // Setup feedback popover
  var adjFeedbackModalTable = $("#modal-adj-table").DataTable({
    {% if adj0.id %}
    'ajax': '{% tournament_url get_adj_feedback %}?id={{ adj0.id }}',
    {% endif %}
    'bPaginate': false,
    'bFilter': false
  });
  $('#table-search').keyup(function(){
    adjFeedbackModalTable.search($(this).val()).draw();
  })

// ALLOCATION INITIALISATION

  load_adjudicator_scores(function() {
    // The below function is the callback to be executed after load_adjudicator_scores()
    load_allocation(function() {
      append_adj_scores();
      load_conflict_data();
    });
  });


//   var conflicts;
//   var scores;























//   /* sorting function for adjs */
//   $.fn.dataTableExt.afnSortData['adj'] = function ( oSettings, iColumn) {
//     var aData = [];
//     $('.adj-holder', oSettings.oApi._fnGetTrNodes(oSettings)).each( function () {
//       var name = $('span', this).html();
//       if (name == null) name = '';
//       aData.push(name);
//     });
//     return aData;
//   };

//   /* filter function for adjs */
//   $.fn.dataTableExt.ofnSearch['adj'] = function ( sData ) {
//     var jo = $(sData);
//     var f = $('span', jo).html();
//     return f;
//   };

//   /////////////////////
//   // HANDLERS
//   ////////////////////










//   // the standard header to be re-added to the modal
//   var tableHead = '<table id="modal-adj-table" class="table"><thead><th><span class="glyphicon glyphicon-time" data-toggle="tooltip" title="Round"></span></th><th><span class="glyphicon glyphicon-sort" data-toggle="tooltip" title="Bracket"></span></th><th>Debate</th><th>Source</th><th>Score</th><th>Comments</th></tr><thead><tbody><tbody><table>'

//   //////////
//   // INIT
//   ////////



});